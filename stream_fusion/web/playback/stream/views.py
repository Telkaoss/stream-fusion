from io import BytesIO
import json
from urllib.parse import unquote
import redis.asyncio as redis
import asyncio
import time
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from redis.exceptions import LockError
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi_simple_rate_limiter import rate_limiter
from fastapi_simple_rate_limiter.database import create_redis_session
from starlette.background import BackgroundTask

from stream_fusion.services.postgresql.dao.apikey_dao import APIKeyDAO
from stream_fusion.services.redis.redis_config import get_redis_cache_dependency
from stream_fusion.utils.cache.local_redis import RedisCache
from stream_fusion.logging_config import logger
from stream_fusion.settings import settings
from stream_fusion.utils.debrid.get_debrid_service import (
    get_debrid_service,
    get_download_service,
)
from stream_fusion.utils.debrid.realdebrid import RealDebrid
from stream_fusion.utils.debrid.torbox import Torbox
from stream_fusion.utils.debrid.stremthrudebrid import StremThruDebrid
from stream_fusion.utils.parse_config import parse_config
from stream_fusion.utils.string_encoding import decodeb64
from stream_fusion.utils.security import check_api_key
from stream_fusion.web.playback.stream.schemas import (
    ErrorResponse,
    HeadResponse,
)

router = APIRouter()

redis_client = redis.Redis(
    host=settings.redis_host, port=settings.redis_port, db=settings.redis_db
)
redis_session = create_redis_session(
    host=settings.redis_host, port=settings.redis_port, db=settings.redis_db
)

DOWNLOAD_IN_PROGRESS_FLAG = "DOWNLOAD_IN_PROGRESS"


class ProxyStreamer:
    def __init__(
        self,
        request: Request,
        url: str,
        headers: dict,
        buffer_size: int = settings.proxy_buffer_size,
    ):
        self.request = request
        self.url = url
        self.headers = headers
        self.response = None
        self.buffer = BytesIO()
        self.buffer_size = buffer_size
        self.eof = False

    async def fill_buffer(self):
        while len(self.buffer.getvalue()) < self.buffer_size and not self.eof:
            chunk = await self.response.content.read(
                self.buffer_size - len(self.buffer.getvalue())
            )
            if not chunk:
                self.eof = True
                break
            self.buffer.write(chunk)
        self.buffer.seek(0)

    async def stream_content(self):
        async with self.request.app.state.http_session.get(
            self.url, headers=self.headers
        ) as self.response:
            while True:
                if self.buffer.tell() == len(self.buffer.getvalue()):
                    if self.eof:
                        break
                    self.buffer = BytesIO()
                    await self.fill_buffer()

                chunk = self.buffer.read(8192)
                if chunk:
                    yield chunk
                else:
                    await asyncio.sleep(0.1)

    async def close(self):
        if self.response:
            await self.response.release()
        logger.debug("Playback: Streaming connection closed")


async def handle_download(
    query: dict, config: dict, ip: str, redis_cache: RedisCache
) -> str:
    api_key = config.get("apiKey")
    cache_key_params = {k: query[k] for k in sorted(query.keys())} 
    cache_key = f"download:{api_key}:{json.dumps(cache_key_params)}_{ip}"

    # Check if a download is already in progress
    if await redis_cache.get(cache_key) == DOWNLOAD_IN_PROGRESS_FLAG:
        logger.info("Playback: Download already in progress")
        return settings.no_cache_video_url

    # Determine the actual download service (could be StremThru even for 'DL' request)
    download_service = get_download_service(config)

    # If StremThru is the configured download handler
    if isinstance(download_service, StremThruDebrid):
        logger.info("Playback (handle_download): StremThru service detected as download handler. Attempting direct stream link retrieval.")
        # Ensure store_code present for StremThruDebrid
        if not query.get('store_code'):
            default_service = config.get('debridDownloader')
            inv = {v: k for k, v in StremThruDebrid.STORE_CODE_TO_NAME.items()}
            code = inv.get(default_service.lower()) if default_service else None
            if code:
                logger.debug(f"Playback: inferred store_code '{code}' for StremThruDebrid based on debridDownloader '{default_service}'")
                query['store_code'] = code
            else:
                logger.warning(f"Playback: cannot infer store_code for StremThruDebrid from config.debridDownloader '{default_service}'")
        try:
            direct_link = await download_service.get_stream_link(query, config, ip)
            if direct_link:
                logger.success(f"Playback (handle_download): StremThru provided direct link: {direct_link[:60]}...")
                # Return the direct link immediately, skip Redis flag and status page
                return direct_link
            else:
                logger.warning("Playback (handle_download): StremThru did not provide an immediate link. Proceeding with background process indication.")
        except Exception as e:
            logger.error(f"Playback (handle_download): Error getting StremThru link: {e}. Proceeding with background process indication.")
            # Fall through to the standard download status logic

    # Mark the start of the download
    await redis_cache.set(
        cache_key, DOWNLOAD_IN_PROGRESS_FLAG, expiration=600  # 10 minute expiration
    )

    try:
        debrid_service = get_download_service(config)
        if not debrid_service:
            raise HTTPException(
                status_code=500, detail="Download service not available"
            )

        if isinstance(debrid_service, RealDebrid):
            torrent_id = debrid_service.add_magnet_or_torrent_and_select(query, ip)
            logger.success(
                f"Playback: Added magnet or torrent to Real-Debrid: {torrent_id}"
            )
            if not torrent_id:
                raise HTTPException(
                    status_code=500,
                    detail="Failed to add magnet or torrent to Real-Debrid",
                )
        elif isinstance(debrid_service, Torbox):
            magnet = query["magnet"]
            torrent_download = (
                unquote(query["torrent_download"])
                if query["torrent_download"] is not None
                else None
            )
            privacy = query.get("privacy", "private")
            torrent_info = debrid_service.add_magnet_or_torrent(
                magnet, torrent_download, ip, privacy
            )
            logger.success(
                f"Playback: Added magnet or torrent to TorBox: {magnet[:50]}"
            )
            if not torrent_info:
                raise HTTPException(
                    status_code=500,
                    detail="Failed to add magnet or torrent to TorBox",
                )
        elif isinstance(debrid_service, StremThruDebrid):
            # StremThru handles caching/downloading via its get_stream_link/add_magnet logic.
            # No explicit background caching call needed here.
            logger.info("Playback: StremThru service detected. Skipping explicit background caching call.")
            pass # Nothing to do here for StremThru in this specific block
        else:
            try:
                # Check if the service supports background caching before attempting to call it
                if hasattr(debrid_service, 'start_background_caching'):
                    if debrid_service.start_background_caching(query):
                        logger.success(
                            f"Playback: Started background caching for magnet: {query['magnet'][:50]}"
                        )
                    else:
                        # If the method exists but returns False, it failed
                        raise HTTPException(
                            status_code=500,
                            detail="Failed to start background caching (service returned false)"
                        )
                else:
                    # Log a warning if the service doesn't support this method
                    logger.warning(f"Playback: Service {type(debrid_service).__name__} does not support start_background_caching. Skipping.")
                    # Since caching isn't started/needed, clear the progress flag immediately.
                    await redis_cache.delete(cache_key)
            except Exception as e:
                logger.error(f"Error starting background caching: {str(e)}")
                # Ensure the flag is deleted on any exception during the caching attempt
                await redis_cache.delete(cache_key)
        return settings.no_cache_video_url
    except Exception as e:
        # Ensure the flag is deleted on any other exception within handle_download
        await redis_cache.delete(cache_key)
        logger.error(f"Playback: Error handling download: {str(e)}", exc_info=True)
        raise e


async def get_stream_link(
    decoded_query: str, config: dict, ip: str, redis_cache: RedisCache, cache_user_identifier: str, request: Request
) -> str:
    logger.debug(f"Playback: Getting stream link for query: {decoded_query}, IP: {ip}")
    cache_key = f"stream_link:{cache_user_identifier}:{decoded_query}"

    cached_link = await redis_cache.get(cache_key)
    if cached_link:
        logger.info(f"Playback: Stream link found in cache: {cached_link}")
        return cached_link

    logger.debug("Playback: Stream link not found in cache, generating new link")

    query = json.loads(decoded_query)
    service = query.get("service", False)

    if service == "DL":
        link = await handle_download(query, config, ip, redis_cache)
    elif service:
        debrid_service = get_debrid_service(config, service, request)
        logger.info(f"Playback: Attempting get_stream_link for service '{type(debrid_service).__name__}' and query {decoded_query}...")
        link = await debrid_service.get_stream_link(query, config, ip)
        logger.info(f"Playback: Service '{type(debrid_service).__name__}' returned link: {link}")
    else:
        logger.error("Playback: Service not found in query")
        raise HTTPException(status_code=500, detail="Service not found in query")

    if link != settings.no_cache_video_url:
        logger.debug(f"Playback: Caching new stream link: {link}")
        await redis_cache.set(cache_key, link, expiration=3600)  # Cache for 1 hour
        logger.info(f"Playback: New stream link generated and cached: {link}")
    else:
        logger.debug("Playback: Stream link not cached (NO_CACHE_VIDEO_URL)")
    return link


# Nouvelle route pour le playback via Stremthru
@router.get("/stremthru/{store_code}/{config}/{query}")
@rate_limiter(limit=settings.playback_limit_requests, seconds=settings.playback_limit_seconds)
async def get_stremthru_playback(
    store_code: str,
    config: str,
    query: str,
    request: Request,
    redis_cache: RedisCache = Depends(get_redis_cache_dependency),
    apikey_dao: APIKeyDAO = Depends(),
):
    start_time = time.time()
    ip = request.client.host
    logger.info(f"Playback GET Stremthru/{store_code}: Request received from {ip}")

    try:
        config_dict = parse_config(config)
        api_key = config_dict.get("apiKey")
        cache_user_identifier = api_key if api_key else ip

        # Valider la clé API si elle existe
        if api_key:
            try:
                await check_api_key(api_key, apikey_dao)
                logger.info(f"Playback GET Stremthru/{store_code}: Valid API key provided by {ip}")
            except HTTPException as e:
                logger.warning(f"Playback GET Stremthru/{store_code}: Invalid API key provided by {ip}. Error: {e.detail}")
                raise e
        else:
            logger.info(f"Playback GET Stremthru/{store_code}: No API key provided by {ip}. Proceeding without validation.")

        if not query:
            raise HTTPException(status_code=400, detail="Query required.")

        decoded_query = decodeb64(query)
        query_dict = json.loads(decoded_query)

        # Récupérer l'instance StremThruDebrid
        # Assurez-vous que StremThruDebrid peut être initialisé correctement avec config_dict
        # et qu'il utilise le bon store_name basé sur la config ou store_code?
        # Pour l'instant, supposons que l'instance StremThru est le gestionnaire principal.
        try:
            stremthru_service = StremThruDebrid(config_dict) 
            # TODO: Vérifier si StremThruDebrid utilise bien le store_code implicitement
            # ou s'il faut le passer/configurer spécifiquement.
            # Il lit `store_name` et `store_auth` de config_dict, donc la config URL doit les contenir.
        except Exception as e:
            logger.error(f"Playback GET Stremthru/{store_code}: Failed to initialize StremThruDebrid: {e}")
            raise HTTPException(status_code=500, detail="Failed to initialize Stremthru service")

        logger.info(f"Playback GET Stremthru/{store_code}: Attempting to get stream link via Stremthru service.")
        logger.debug(f"Playback GET Stremthru/{store_code}: Query details: {query_dict}")

        # Add store_code to query_dict before passing it
        query_dict['store_code'] = store_code

        # Appeler get_stream_link qui gère tout le processus (add, wait, unrestrict)
        # Elle prend query (dict), config (dict), ip (str)
        stream_link = await stremthru_service.get_stream_link(query=query_dict, config=config_dict, ip=ip)

        if not stream_link:
            logger.error(f"Playback GET Stremthru/{store_code}: Failed to get stream link from Stremthru service.")
            raise HTTPException(status_code=502, detail="Failed to get stream link from Stremthru service")

        logger.info(f"Playback GET Stremthru/{store_code}: Stream link obtained: {stream_link[:60]}...")

        # Rediriger ou Proxy
        if settings.proxied_link:
            logger.info(f"Playback GET Stremthru/{store_code}: Proxying stream from {stream_link[:60]}...")
            headers = {key: value for key, value in request.headers.items() if key.lower() in ["range", "accept"]}
            streamer = ProxyStreamer(request, stream_link, headers)
            return StreamingResponse(
                streamer.stream_content(),
                headers={"Accept-Ranges": "bytes", "Content-Range": request.headers.get("range", "bytes 0-")},
                background=BackgroundTask(streamer.close),
            )
        else:
            logger.info(f"Playback GET Stremthru/{store_code}: Redirecting to {stream_link[:60]}...")
            return RedirectResponse(url=stream_link, status_code=302)

    except HTTPException: # Re-raise HTTP exceptions
        raise
    except Exception as e:
        logger.error(f"Playback GET Stremthru/{store_code}: Unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error during Stremthru playback: {e}")
    finally:
        end_time = time.time()
        logger.info(f"Playback GET Stremthru/{store_code}: Request processing time: {end_time - start_time:.2f} seconds")

# Nouvelle route HEAD pour Stremthru
@router.head("/stremthru/{store_code}/{config}/{query}")
async def head_stremthru_playback(
    store_code: str,
    config: str,
    query: str,
    request: Request,
    apikey_dao: APIKeyDAO = Depends(), # Ajouter dépendances nécessaires
):
    ip = request.client.host
    logger.info(f"Playback HEAD Stremthru/{store_code}: Request received from {ip}")

    try:
        config_dict = parse_config(config)
        api_key = config_dict.get("apiKey")

        # Valider la clé API si elle existe
        if api_key:
            try:
                await check_api_key(api_key, apikey_dao)
                logger.info(f"Playback HEAD Stremthru/{store_code}: Valid API key provided by {ip}")
            except HTTPException as e:
                logger.warning(f"Playback HEAD Stremthru/{store_code}: Invalid API key provided by {ip}. Error: {e.detail}")
                raise e
        else:
            logger.info(f"Playback HEAD Stremthru/{store_code}: No API key provided by {ip}. Proceeding without validation.")

        if not query:
            raise HTTPException(status_code=400, detail="Query required.")
        
        # La logique HEAD est simple: renvoyer OK avec les bons en-têtes.
        # On ne vérifie PAS le lien distant ici pour Stremthru pour l'instant.
        # Le client (Stremio) fera la requête GET si le HEAD réussit.
        headers = {
            "Content-Type": "video/mp4",
            "Accept-Ranges": "bytes",
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
            "Content-Length": "0" # Indiquer une taille 0 car on ne connaît pas la vraie taille
        }
        logger.info(f"Playback HEAD Stremthru/{store_code}: Returning 200 OK for HEAD request.")
        return Response(status_code=status.HTTP_200_OK, headers=headers)

    except HTTPException: # Re-raise HTTP exceptions
        raise
    except Exception as e:
        logger.error(f"Playback HEAD Stremthru/{store_code}: Unexpected error: {e}", exc_info=True)
        return Response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=ErrorResponse(
                detail="An error occurred while processing the HEAD request."
            ).model_dump_json(),
            media_type="application/json",
        )


@router.get("/{config}/{query}", responses={500: {"model": ErrorResponse}})
@rate_limiter(limit=20, seconds=60, redis=redis_session)
async def get_playback(
    config: str,
    query: str,
    request: Request,
    redis_cache: RedisCache = Depends(get_redis_cache_dependency),
    apikey_dao: APIKeyDAO = Depends(),
):
    try:
        config = parse_config(config)
        api_key = config.get("apiKey")
        ip = request.client.host
        cache_user_identifier = api_key if api_key else ip

        # Only validate the API key if it exists
        if api_key:
            try:
                await check_api_key(api_key, apikey_dao)
                logger.info(f"Playback: Valid API key provided by {ip}")
            except HTTPException as e:
                logger.warning(f"Playback: Invalid API key provided by {ip}. Error: {e.detail}")
                raise e # Re-raise if validation fails for a provided key
        else:
            logger.info(f"Playback: No API key provided by {ip}. Proceeding without API key validation.")

        if not query:
            logger.warning("Playback: Query is empty")
            raise HTTPException(status_code=400, detail="Query required.")

        decoded_query = decodeb64(query)
        logger.debug(f"Playback: Decoded query: {decoded_query}, Client IP: {ip}")

        query_dict = json.loads(decoded_query)
        logger.debug(f"Playback: Received playback request for query: {decoded_query}")
        service = query_dict.get("service", False)

        if service == "DL":
            # Pass cache_user_identifier to handle_download if needed, otherwise it uses ip/query_dict
            link = await handle_download(query_dict, config, ip, redis_cache)
            return RedirectResponse(url=link, status_code=status.HTTP_302_FOUND)

        # Use cache_user_identifier for lock key
        lock_key = f"lock:stream:{cache_user_identifier}:{decoded_query}"
        lock = redis_client.lock(lock_key, timeout=60)

        try:
            if await lock.acquire(blocking=False):
                logger.debug("Playback: Lock acquired, getting stream link")
                # Pass cache_user_identifier and request to get_stream_link
                link = await get_stream_link(decoded_query, config, ip, redis_cache, cache_user_identifier, request)
            else:
                logger.debug("Playback: Lock not acquired, waiting for cached link")
                # Use cache_user_identifier for cache key lookup
                cache_key = f"stream_link:{cache_user_identifier}:{decoded_query}"
                for _ in range(30):
                    await asyncio.sleep(1)
                    cached_link = await redis_cache.get(cache_key)
                    if cached_link:
                        logger.debug("Playback: Cached link found while waiting")
                        link = cached_link
                        break
                else:
                    logger.warning("Playback: Timed out waiting for cached link")
                    raise HTTPException(
                        status_code=503,
                        detail="Service temporarily unavailable. Please try again.",
                    )
        finally:
            try:
                await lock.release()
                logger.debug("Playback: Lock released")
            except LockError:
                logger.warning("Playback: Failed to release lock (already released)")

        if not settings.proxied_link:
            logger.debug(f"Playback: Redirecting to non-proxied link: {link}")
            return RedirectResponse(
                url=link, status_code=status.HTTP_301_MOVED_PERMANENTLY
            )

        if service == "TB":  # TODO: Check this for torbox
            logger.debug("Playback: Bypass proxied link for TorBox")
            return RedirectResponse(url=link, status_code=status.HTTP_302_FOUND)

        logger.debug("Playback: Preparing to proxy stream")
        headers = {}
        range_header = request.headers.get("range")
        if range_header and "=" in range_header:
            logger.debug(f"Playback: Range header found: {range_header}")
            range_value = range_header.strip().split("=")[1]
            range_parts = range_value.split("-")
            start = int(range_parts[0]) if range_parts[0] else 0
            end = int(range_parts[1]) if len(range_parts) > 1 and range_parts[1] else ""
            headers["Range"] = f"bytes={start}-{end}"
            logger.debug(f"Playback: Range header set: {headers['Range']}")

        streamer = ProxyStreamer(request, link, headers)

        logger.debug(f"Playback: Initiating request to: {link}")
        async with request.app.state.http_session.head(
            link, headers=headers
        ) as response:
            logger.debug(f"Playback: Response status: {response.status}")
            stream_headers = {
                "Content-Type": "video/mp4",
                "Accept-Ranges": "bytes",
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Connection": "keep-alive",
                "Content-Disposition": "inline",
                "Access-Control-Allow-Origin": "*",
            }

            if response.status == 206:
                logger.debug("Playback: Partial content response")
                stream_headers["Content-Range"] = response.headers["Content-Range"]

            for header in ["Content-Length", "ETag", "Last-Modified"]:
                if header in response.headers:
                    stream_headers[header] = response.headers[header]
                    logger.debug(
                        f"Playback: Header set: {header}: {stream_headers[header]}"
                    )

            logger.debug("Playback: Preparing streaming response")
            return StreamingResponse(
                streamer.stream_content(),
                status_code=206 if "Range" in headers else 200,
                headers=stream_headers,
                background=BackgroundTask(streamer.close),
            )

    except Exception as e:
        logger.error(f"Playback: Playback error: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=ErrorResponse(
                detail="An error occurred while processing the request."
            ).model_dump(),
        )


@router.head(
    "/{config}/{query}",
    response_model=HeadResponse,
    responses={500: {"model": ErrorResponse}, 202: {"model": None}},
)
async def head_playback(
    config: str,
    query: str,
    request: Request,
    redis_cache: RedisCache = Depends(get_redis_cache_dependency),
    apikey_dao: APIKeyDAO = Depends(),
):
    try:
        config = parse_config(config)
        api_key = config.get("apiKey")
        ip = request.client.host
        cache_user_identifier = api_key if api_key else ip

        # Only validate the API key if it exists
        if api_key:
            try:
                await check_api_key(api_key, apikey_dao)
                logger.info(f"Playback HEAD: Valid API key provided by {ip}")
            except HTTPException as e:
                logger.warning(f"Playback HEAD: Invalid API key provided by {ip}. Error: {e.detail}")
                raise e # Re-raise if validation fails for a provided key
        else:
            # Don't raise 401, just log if no key is provided
            logger.info(f"Playback HEAD: No API key provided by {ip}. Proceeding without API key validation.")

        if not query:
            raise HTTPException(status_code=400, detail="Query required.")

        decoded_query = decodeb64(query)
        query_dict = json.loads(decoded_query)
        service = query_dict.get("service", False)

        headers = {
            "Content-Type": "video/mp4",
            "Accept-Ranges": "bytes",
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
        }

        if service == "DL":
            # Check if download is in progress using cache_user_identifier
            download_cache_key = f"download:{cache_user_identifier}:{json.dumps(query_dict)}"
            if await redis_cache.get(download_cache_key) == DOWNLOAD_IN_PROGRESS_FLAG:
                logger.info("Playback: Download in progress, returning 202 Accepted")
                return Response(status_code=status.HTTP_202_ACCEPTED, headers=headers)
            else:
                logger.info("Playback: Download not started, returning 200 OK")
                return Response(status_code=status.HTTP_200_OK, headers=headers)

        # Use cache_user_identifier for stream link cache key
        cache_key = f"stream_link:{cache_user_identifier}:{decoded_query}"

        for _ in range(30):
            if await redis_cache.exists(cache_key):
                link = await redis_cache.get(cache_key)

                if (
                    not settings.proxied_link
                ):  # avoid sending HEAD request if link is sent directly
                    return Response(status_code=status.HTTP_200_OK, headers=headers)

                async with request.app.state.http_session.head(link) as response:
                    if response.status == 200:
                        headers["Content-Length"] = response.headers.get(
                            "Content-Length", "0"
                        )
                        return Response(status_code=status.HTTP_200_OK, headers=headers)

            await asyncio.sleep(1)

        return Response(status_code=status.HTTP_202_ACCEPTED, headers=headers)

    except redis.ConnectionError as e:
        logger.error(f"Playback: Redis connection error: {e}")
        return Response(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content="Service temporarily unavailable",
        )
    except Exception as e:
        logger.error(f"Playback: HEAD request error: {e}")
        return Response(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=ErrorResponse(
                detail="An error occurred while processing the request."
            ).model_dump_json(),
            media_type="application/json",
        )
