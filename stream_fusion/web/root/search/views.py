import hashlib
import time
from fastapi import APIRouter, Depends, HTTPException, Request
from uuid import UUID
import asyncio


from stream_fusion.services.postgresql.dao.apikey_dao import APIKeyDAO
from stream_fusion.services.postgresql.dao.torrentitem_dao import TorrentItemDAO
from stream_fusion.services.redis.redis_config import get_redis_cache_dependency
from stream_fusion.utils.cache.cache import search_public
from stream_fusion.utils.cache.local_redis import RedisCache
from stream_fusion.utils.debrid.get_debrid_service import get_all_debrid_services
from stream_fusion.utils.filter.results_per_quality_filter import (
    ResultsPerQualityFilter,
)
from stream_fusion.utils.filter_results import (
    filter_items,
    merge_items,
    sort_items,
)
from stream_fusion.logging_config import logger
from stream_fusion.utils.jackett.jackett_result import JackettResult
from stream_fusion.utils.jackett.jackett_service import JackettService
from stream_fusion.utils.parser.parser_service import StreamParser
from stream_fusion.utils.sharewood.sharewood_service import SharewoodService
from stream_fusion.utils.yggfilx.yggflix_service import YggflixService
from stream_fusion.utils.metdata.cinemeta import Cinemeta
from stream_fusion.utils.metdata.tmdb import TMDB
from stream_fusion.utils.models.movie import Movie
from stream_fusion.utils.models.series import Series
from stream_fusion.utils.parse_config import parse_config
from stream_fusion.utils.security.security_api_key import check_api_key
from stream_fusion.utils.torrent.torrent_item import TorrentItem
from stream_fusion.web.root.search.schemas import SearchResponse, Stream
from stream_fusion.web.root.search.stremio_parser import parse_to_stremio_streams
from stream_fusion.utils.torrent.torrent_service import TorrentService
from stream_fusion.utils.torrent.torrent_smart_container import TorrentSmartContainer
from stream_fusion.utils.zilean.zilean_result import ZileanResult
from stream_fusion.utils.zilean.zilean_service import ZileanService
from stream_fusion.settings import settings


router = APIRouter()


async def full_prefetch_from_cache(media, config, redis_cache, stream_cache_key, get_metadata, stream_type, debrid_services, torrent_dao, request):
    """Pre-fetch complet de l'épisode suivant en arrière-plan"""
    try:
        # Petit délai pour ne pas surcharger immédiatement après la recherche principale
        await asyncio.sleep(1.0)
        
        current_season_num = int(media.season.replace("S", ""))
        current_episode_num = int(media.episode.replace("E", ""))
        next_episode_num = current_episode_num + 1
        
        next_episode_id = f"{media.id.split(':')[0]}:{current_season_num}:{next_episode_num}"
        
        next_media_mock = type(media)(
            id=next_episode_id,
            tmdb_id=media.tmdb_id,
            titles=media.titles,
            season=f"S{current_season_num:02d}",
            episode=f"E{next_episode_num:02d}",
            languages=media.languages
        )
        
        next_stream_key = stream_cache_key(next_media_mock)
        cached_next = await redis_cache.get(next_stream_key)
        
        if cached_next is None:
            logger.debug(f"Pre-fetch: Starting full background search for next episode {next_episode_id}")
            
            next_media = await asyncio.wait_for(
                redis_cache.get_or_set(lambda: get_metadata(next_episode_id, stream_type), next_episode_id, stream_type, config["metadataProvider"]),
                timeout=5.0
            )
            
            search_results = []
            
            background_session = request.app.state.db_session_factory()
            try:
                background_torrent_dao = TorrentItemDAO(background_session)
                torrent_service = TorrentService(config, background_torrent_dao)
                
                # Zilean
                if config["zilean"]:
                    zilean_service = ZileanService(config)
                    zilean_search_results = zilean_service.search(next_media)
                    if zilean_search_results:
                        zilean_search_results = [
                            ZileanResult().from_api_cached_item(torrent, next_media)
                            for torrent in zilean_search_results
                            if len(getattr(torrent, "info_hash", "")) == 40
                        ]
                        zilean_search_results = filter_items(zilean_search_results, next_media, config=config)
                        zilean_search_results = await torrent_service.convert_and_process(zilean_search_results)
                        search_results = merge_items(search_results, zilean_search_results)
                
                # YggFlix
                if config["yggflix"] and len(search_results) < int(config["minCachedResults"]):
                    yggflix_service = YggflixService(config)
                    yggflix_search_results = yggflix_service.search(next_media)
                    if yggflix_search_results:
                        yggflix_search_results = filter_items(yggflix_search_results, next_media, config=config)
                        yggflix_search_results = await torrent_service.convert_and_process(yggflix_search_results)
                        search_results = merge_items(search_results, yggflix_search_results)
                
                if search_results:
                    # Traitement des résultats
                    filtered_results = ResultsPerQualityFilter(config).filter(search_results)
                    torrent_smart_container = TorrentSmartContainer(filtered_results, next_media)
                    
                    # Vérifier la disponibilité
                    for debrid in debrid_services:
                        hashes = torrent_smart_container.get_unaviable_hashes()
                        ip = request.client.host
                        result = debrid.get_availability_bulk(hashes, ip)
                        if result:
                            torrent_smart_container.update_availability(result, type(debrid), next_media)
                    
                    # Cache et génération des streams
                    if config["cache"]:
                        torrent_smart_container.cache_container_items()
                    
                    best_matching_results = torrent_smart_container.get_best_matching()
                    best_matching_results = sort_items(best_matching_results, config)
                    
                    parser = StreamParser(config)
                    stream_list = parser.parse_to_stremio_streams(best_matching_results, next_media)
                    next_stream_objects = [Stream(**stream) for stream in stream_list]
                    
                    # Mettre en cache les résultats
                    await redis_cache.set(stream_cache_key(next_media), next_stream_objects, expiration=1200)
                    logger.success(f"Pre-fetch: Successfully background pre-cached {len(next_stream_objects)} streams for episode {next_episode_id}")
                else:
                    logger.debug(f"Pre-fetch: No results found for episode {next_episode_id}")
                    
            finally:
                await background_session.commit()
                await background_session.close()
                
        else:
            logger.debug(f"Pre-fetch: Next episode {next_episode_id} already cached")
            
    except Exception as e:
        logger.debug(f"Pre-fetch: Error during full background pre-fetch: {str(e)}")


async def simple_prefetch_next_episode(media, config, redis_cache, stream_cache_key, get_metadata, stream_type):
    """Pre-fetch simple de l'épisode suivant en arrière-plan"""
    try:
        await asyncio.sleep(0.5)
        
        current_season_num = int(media.season.replace("S", ""))
        current_episode_num = int(media.episode.replace("E", ""))
        next_episode_num = current_episode_num + 1
        
        next_episode_id = f"{media.id.split(':')[0]}:{current_season_num}:{next_episode_num}"
        
        next_media_mock = type(media)(
            id=next_episode_id,
            tmdb_id=media.tmdb_id,
            titles=media.titles,
            season=f"S{current_season_num:02d}",
            episode=f"E{next_episode_num:02d}",
            languages=media.languages
        )
        
        next_stream_key = stream_cache_key(next_media_mock)
        cached_next = await redis_cache.get(next_stream_key)
        
        if cached_next is None:
            logger.debug(f"Pre-fetch: Starting simple background search for next episode {next_episode_id}")
            
            await asyncio.wait_for(
                redis_cache.get_or_set(lambda: get_metadata(next_episode_id, stream_type), next_episode_id, stream_type, config["metadataProvider"]),
                timeout=3.0
            )
            logger.debug(f"Pre-fetch: Metadata cached for episode {next_episode_id}")
            
        else:
            logger.debug(f"Pre-fetch: Next episode {next_episode_id} already cached")
            
    except asyncio.TimeoutError:
        logger.debug(f"Pre-fetch: Timeout during simple background pre-fetch")
    except Exception as e:
        logger.debug(f"Pre-fetch: Error during simple background pre-fetch: {str(e)}")


async def prefetch_next_episode(media, config, redis_cache, stream_cache_key, get_metadata, get_and_filter_results, stream_processing, ResultsPerQualityFilter, Stream, stream_type):
    """Pre-fetch l'épisode suivant en arrière-plan"""
    try:
        current_season_num = int(media.season.replace("S", ""))
        current_episode_num = int(media.episode.replace("E", ""))
        next_episode_num = current_episode_num + 1
        
        next_episode_id = f"{media.id.split(':')[0]}:{current_season_num}:{next_episode_num}"
        
        next_media_mock = type(media)(
            id=next_episode_id,
            tmdb_id=media.tmdb_id,
            titles=media.titles,
            season=f"S{current_season_num:02d}",
            episode=f"E{next_episode_num:02d}",
            languages=media.languages
        )
        
        next_stream_key = stream_cache_key(next_media_mock)
        cached_next = await redis_cache.get(next_stream_key)
        
        if cached_next is None:
            logger.info(f"Pre-fetch: Starting background search for next episode {next_episode_id}")
            
            expiration_time = 1200
            
            next_media = await asyncio.wait_for(
                redis_cache.get_or_set(lambda: get_metadata(next_episode_id, stream_type), next_episode_id, stream_type, config["metadataProvider"]),
                timeout=8.0
            )
            
            raw_results = await asyncio.wait_for(
                get_and_filter_results(next_media, config),
                timeout=12.0
            )
            filtered_results = ResultsPerQualityFilter(config).filter(raw_results)
            next_streams = stream_processing(filtered_results, next_media, config)
            next_stream_objects = [Stream(**stream) for stream in next_streams]
            
            await redis_cache.set(stream_cache_key(next_media), next_stream_objects, expiration=expiration_time)
            logger.success(f"Pre-fetch: Successfully background pre-cached {len(next_stream_objects)} streams for episode {next_episode_id}")
            
        else:
            logger.debug(f"Pre-fetch: Next episode {next_episode_id} already cached")
            
    except asyncio.TimeoutError:
        logger.debug(f"Pre-fetch: Timeout during background pre-fetch")
    except Exception as e:
        # Erreurs courantes qui ne nécessitent pas d'alerte
        error_msg = str(e).lower()
        if any(keyword in error_msg for keyword in ["connection", "timeout", "reset", "closed"]):
            logger.debug(f"Pre-fetch: Network issue during background pre-fetch: {str(e)}")
        else:
            logger.warning(f"Pre-fetch: Error during background pre-fetch: {str(e)}")


@router.get("/{config}/stream/{stream_type}/{stream_id}", response_model=SearchResponse)
async def get_results(
    request: Request,
    config: str,
    stream_type: str,
    stream_id: str,
    redis_cache: RedisCache = Depends(get_redis_cache_dependency),
    apikey_dao: APIKeyDAO = Depends(),
    torrent_dao: TorrentItemDAO = Depends(),
) -> SearchResponse:
    start = time.time()
    logger.info(f"Search: Stream request initiated for {stream_type} - {stream_id}")

    stream_id = stream_id.replace(".json", "")
    config = parse_config(config)
    api_key = config.get("apiKey")
    ip_address = request.client.host
    # Only validate the API key if it exists
    if api_key:
        await check_api_key(api_key, apikey_dao)
    else:
        logger.warning("Search: API key not found in config.")
        raise HTTPException(status_code=401, detail="API key not found in config.")

    debrid_services = get_all_debrid_services(config)
    logger.debug(f"Search: Found {len(debrid_services)} debrid services")
    logger.info(
        f"Search: Debrid services: {[debrid.__class__.__name__ for debrid in debrid_services]}"
    )

    def get_metadata(episode_id=None, media_type=None):
        logger.info(f"Search: Fetching metadata from {config['metadataProvider']}")
        if config["metadataProvider"] == "tmdb" and settings.tmdb_api_key:
            metadata_provider = TMDB(config)
        else:
            metadata_provider = Cinemeta(config)
        actual_id = episode_id if episode_id is not None else stream_id
        actual_type = media_type if media_type is not None else stream_type
        return metadata_provider.get_metadata(actual_id, actual_type)

    media = await redis_cache.get_or_set(
        get_metadata, stream_id, stream_type, config["metadataProvider"]
    )
    logger.debug(f"Search: Retrieved media metadata for {str(media.titles)}")

    def stream_cache_key(media):
        cache_user_identifier = api_key if api_key else ip_address
        if isinstance(media, Movie):
            key_string = f"stream:{cache_user_identifier}:{media.titles[0]}:{media.year}:{media.languages[0]}"
        elif isinstance(media, Series):
            key_string = f"stream:{cache_user_identifier}:{media.titles[0]}:{media.languages[0]}:{media.season}{media.episode}"
        else:
            logger.error("Search: Only Movie and Series are allowed as media!")
            raise HTTPException(
                status_code=500, detail="Only Movie and Series are allowed as media!"
            )
        hashed_key = hashlib.sha256(key_string.encode("utf-8")).hexdigest()
        return hashed_key[:16]

    cached_result = await redis_cache.get(stream_cache_key(media))
    if cached_result is not None:
        logger.info("Search: Returning cached processed results")
        
        if isinstance(media, Series):
            asyncio.create_task(full_prefetch_from_cache(media, config, redis_cache, stream_cache_key, get_metadata, stream_type, debrid_services, torrent_dao, request))
            await asyncio.sleep(0.5)  # 500ms de délai pour les séries
            
        total_time = time.time() - start
        logger.success(f"Search: Request completed in {total_time:.2f} seconds")
        return SearchResponse(streams=cached_result)

    def media_cache_key(media):
        if isinstance(media, Movie):
            key_string = f"media:{media.titles[0]}:{media.year}:{media.languages[0]}"
        elif isinstance(media, Series):
            key_string = f"media:{media.titles[0]}:{media.languages[0]}:{media.season}{media.episode}"
        else:
            raise TypeError("Only Movie and Series are allowed as media!")
        hashed_key = hashlib.sha256(key_string.encode("utf-8")).hexdigest()
        return hashed_key[:16]

    async def get_search_results(media, config):
        search_results = []
        torrent_service = TorrentService(config, torrent_dao)

        async def perform_search(update_cache=False):
            nonlocal search_results
            search_results = []

            if config["cache"] and not update_cache:
                public_cached_results = search_public(media)
                if public_cached_results:
                    logger.success(
                        f"Search: Found {len(public_cached_results)} public cached results"
                    )
                    public_cached_results = [
                        JackettResult().from_cached_item(torrent, media)
                        for torrent in public_cached_results
                        if isinstance(torrent, dict) and len(torrent.get("hash", "")) == 40
                    ]
                    public_cached_results = filter_items(
                        public_cached_results, media, config=config
                    )
                    public_cached_results = await torrent_service.convert_and_process(
                        public_cached_results
                    )
                    search_results.extend(public_cached_results)

            # Prioriser Zilean en premier
            if config["zilean"]:
                zilean_service = ZileanService(config)
                zilean_search_results = zilean_service.search(media)
                if zilean_search_results:
                    logger.success(
                        f"Search: Found {len(zilean_search_results)} results from Zilean"
                    )
                    zilean_search_results = [
                        ZileanResult().from_api_cached_item(torrent, media)
                        for torrent in zilean_search_results
                        if len(getattr(torrent, "info_hash", "")) == 40
                    ]
                    zilean_search_results = filter_items(
                        zilean_search_results, media, config=config
                    )
                    zilean_search_results = await torrent_service.convert_and_process(
                        zilean_search_results
                    )
                    logger.info(
                        f"Search: Zilean final search results: {len(zilean_search_results)}"
                    )
                    search_results = merge_items(search_results, zilean_search_results)

            # Ensuite YggFlix si pas assez de résultats
            if config["yggflix"] and len(search_results) < int(
                config["minCachedResults"]
            ):
                yggflix_service = YggflixService(config)
                yggflix_search_results = yggflix_service.search(media)
                if yggflix_search_results:
                    logger.success(
                        f"Search: Found {len(yggflix_search_results)} results from YggFlix"
                    )
                    yggflix_search_results = filter_items(
                        yggflix_search_results, media, config=config
                    )
                    yggflix_search_results = await torrent_service.convert_and_process(
                        yggflix_search_results
                    )
                    search_results = merge_items(search_results, yggflix_search_results)

            if config["sharewood"] and len(search_results) < int(
                config["minCachedResults"]
            ):
                try:
                    sharewood_service = SharewoodService(config)
                    sharewood_search_results = sharewood_service.search(media)
                    if sharewood_search_results:
                        logger.success(
                            f"Search: Found {len(sharewood_search_results)} results from Sharewood"
                        )
                        sharewood_search_results = filter_items(
                            sharewood_search_results, media, config=config
                        )
                        sharewood_search_results = (
                            await torrent_service.convert_and_process(
                                sharewood_search_results
                            )
                        )
                        search_results = merge_items(search_results, sharewood_search_results)
                except Exception as e:
                    logger.warning(f"Search: Sharewood search failed, skipping: {str(e)}")

            if config["jackett"] and len(search_results) < int(
                config["minCachedResults"]
            ):
                jackett_service = JackettService(config)
                jackett_search_results = jackett_service.search(media)
                logger.success(
                    f"Search: Found {len(jackett_search_results)} results from Jackett"
                )
                filtered_jackett_search_results = filter_items(
                    jackett_search_results, media, config=config
                )
                if filtered_jackett_search_results:
                    torrent_results = await torrent_service.convert_and_process(
                        filtered_jackett_search_results
                    )
                    search_results = merge_items(search_results, torrent_results)

            if update_cache and search_results:
                logger.info(
                    f"Search: Updating cache with {len(search_results)} results"
                )
                try:
                    cache_key = media_cache_key(media)
                    search_results_dict = [item.to_dict() for item in search_results]
                    await redis_cache.set(cache_key, search_results_dict, expiration=settings.redis_expiration)
                    logger.success("Search: Cache update successful")
                except Exception as e:
                    logger.error(f"Search: Error updating cache: {e}")

        await perform_search()
        return search_results

    async def get_and_filter_results(media, config):
        min_results = int(config.get("minCachedResults", 5))
        cache_key = media_cache_key(media)

        unfiltered_results = await redis_cache.get(cache_key)
        if unfiltered_results is None:
            logger.debug("Search: No results in cache. Performing new search.")
            nocache_results = await get_search_results(media, config)
            nocache_results_dict = [item.to_dict() for item in nocache_results]
            await redis_cache.set(cache_key, nocache_results_dict, expiration=settings.redis_expiration)
            logger.info(
                f"Search: New search completed, found {len(nocache_results)} results"
            )
            return nocache_results
        else:
            logger.info(
                f"Search: Retrieved {len(unfiltered_results)} results from redis cache"
            )
            unfiltered_results = [
                TorrentItem.from_dict(item) for item in unfiltered_results
            ]

        filtered_results = filter_items(unfiltered_results, media, config=config)

        if len(filtered_results) < min_results:
            logger.info(
                f"Search: Insufficient filtered results ({len(filtered_results)}). Performing new search."
            )
            await redis_cache.delete(cache_key)
            unfiltered_results = await get_search_results(media, config)
            unfiltered_results_dict = [item.to_dict() for item in unfiltered_results]
            await redis_cache.set(cache_key, unfiltered_results_dict, expiration=settings.redis_expiration)
            filtered_results = filter_items(unfiltered_results, media, config=config)

        logger.success(
            f"Search: Final number of filtered results: {len(filtered_results)}"
        )
        return filtered_results

    raw_search_results = await get_and_filter_results(media, config)
    logger.debug(f"Search: Filtered search results: {len(raw_search_results)}")
    search_results = ResultsPerQualityFilter(config).filter(raw_search_results)
    logger.info(f"Search: Filtered search results per quality: {len(search_results)}")

    def stream_processing(search_results, media, config):
        torrent_smart_container = TorrentSmartContainer(search_results, media)

        if config["debrid"]:
            for debrid in debrid_services:
                hashes = torrent_smart_container.get_unaviable_hashes()
                ip = request.client.host
                result = debrid.get_availability_bulk(hashes, ip)
                if result:
                    torrent_smart_container.update_availability(
                        result, type(debrid), media
                    )
                    # Gérer à la fois les dictionnaires et les listes
                    if isinstance(result, dict):
                        count = len(result.items())
                    else:  # Si c'est une liste (comme pour StremThru)
                        count = len(result)
                    
                    # Déterminer si c'est StremThru pour ajuster la durée du cache
                    is_stremthru = (type(debrid).__name__ == "StremThru" or 
                                   hasattr(debrid, 'store_name') and getattr(debrid, 'store_name', None) is not None)
                    
                    logger.info(
                        f"Search: Checked availability for {count} items with {type(debrid).__name__}"
                    )
                else:
                    logger.warning(
                        "Search: No availability results found in debrid service"
                    )

        if config["cache"]:
            logger.info("Search: Caching public container items")
            torrent_smart_container.cache_container_items()

        best_matching_results = torrent_smart_container.get_best_matching()
        best_matching_results = sort_items(best_matching_results, config)
        logger.info(f"Search: Found {len(best_matching_results)} best matching results")

        parser = StreamParser(config)
        stream_list = parser.parse_to_stremio_streams(best_matching_results, media)
        logger.success(f"Search: Processed {len(stream_list)} streams for Stremio")

        return stream_list

    stream_list = stream_processing(search_results, media, config)
    streams = [Stream(**stream) for stream in stream_list]
    
    # Définir la durée d'expiration par défaut à 1200 secondes
    expiration_time = 1200
    
    # Vérifier si StremThru est utilisé pour ajuster la durée du cache
    has_stremthru = False
    for debrid in debrid_services:
        if type(debrid).__name__ == "StremThru" or hasattr(debrid, 'store_name'):
            has_stremthru = True
            break
    
    # Si StremThru est utilisé, utiliser la durée de cache spécifique
    if has_stremthru:
        expiration_time = 600
        logger.info(f"Search: Using reduced cache expiration time of {expiration_time} seconds for StremThru")
    
    # Mettre en cache les résultats IMMÉDIATEMENT
    await redis_cache.set(stream_cache_key(media), streams, expiration=expiration_time)
    
    # Pre-fetch complet de l'épisode suivant en arrière-plan (non-bloquant)
    if isinstance(media, Series):
        asyncio.create_task(full_prefetch_from_cache(media, config, redis_cache, stream_cache_key, get_metadata, stream_type, debrid_services, torrent_dao, request))
    

    
    total_time = time.time() - start
    logger.info(f"Search: Request completed in {total_time:.2f} seconds")
    return SearchResponse(streams=streams)
