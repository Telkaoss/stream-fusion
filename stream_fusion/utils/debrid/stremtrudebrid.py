import re
import time
import asyncio
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import urljoin, quote_plus
import aiohttp
import logging
import json
from fastapi import HTTPException

from stream_fusion.utils.debrid.base_debrid import BaseDebrid
from stream_fusion.settings import settings

logger = logging.getLogger(__name__)

class StremThruDebrid(BaseDebrid):
    """Debrid proxy via StremThru for multiple stores."""
    STORE_CODE_TO_NAME = {
        'rd': 'realdebrid',
        'ad': 'alldebrid',
        'pz': 'premiumize',
        'tb': 'torbox',
        'dl': 'debridlink',
        'en': 'easydebrid',
        'oc': 'offcloud',
        'pp': 'pikpak',
    }
    STORE_NAME_TO_TOKEN_KEY = {
        'realdebrid': 'RDToken',
        'alldebrid': 'ADToken',
        'premiumize': 'PMToken',
        'torbox': 'TBToken',
        'debridlink': 'DLToken',
        'easydebrid': 'EDToken',
        'offcloud': 'OCToken',
        'pikpak': 'PKToken',
    }

    def __init__(self, config: Dict[str, Any], session: aiohttp.ClientSession = None):
        super().__init__(config, session=session)
        # Determine base URL: try settings first, then config
        base = getattr(settings, 'stremthru_base_url', None) or config.get('stremthru_url')
        if not base:
            logger.warning("StremThruDebrid: URL not specified; proxy calls may fail.")
            self.base_url = ''
        else:
            self.base_url = base.rstrip('/')
        logger.info(f"StremThruDebrid initialized with base URL {self.base_url or '<none>'}")

    async def _request(
        self,
        method: str,
        path: str,
        store_name: str,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        timeout: int = 20,
        max_retries: int = 3,
    ) -> Any:
        """Central async HTTP request to StremThru API."""
        token_key = self.STORE_NAME_TO_TOKEN_KEY.get(store_name)
        if not token_key:
            logger.error(f"_request: unknown store_name '{store_name}'")
            raise HTTPException(status_code=400, detail=f"Unknown store: {store_name}")
        token = self.config.get(token_key)
        url = urljoin(self.base_url + '/', path.lstrip('/'))
        headers = {"Accept": "application/json"}
        if token:
            headers.update({
                "X-StremThru-Store-Name": store_name,
                "X-StremThru-Store-Authorization": f"Bearer {token}"
            })
        if json:
            headers["Content-Type"] = "application/json"
        for attempt in range(max_retries):
            try:
                async with aiohttp.ClientSession(headers=headers) as session:
                    resp = await session.request(method, url, params=params, json=json, timeout=timeout)
                    text = await resp.text()
                    # Return empty dict for missing torrent (404) on GET magnet info
                    if resp.status == 404 and method.upper() == 'GET' and '/magnets/' in path:
                        return {}
                    # Handle error status
                    if resp.status >= 400:
                        # Try parse error payload
                        try:
                            err = await resp.json(content_type=None)
                            code = err.get('error', {}).get('code', resp.status)
                            msg = err.get('error', {}).get('message', text)
                        except:
                            code = resp.status; msg = text
                        raise aiohttp.ClientResponseError(
                            status=resp.status, request_info=resp.request_info,
                            history=(), message=msg
                        )
                    # Return raw text for unrestrict links
                    if 'unrestrict' in path:
                        return text
                    # Parse JSON response
                    try:
                        return await resp.json(content_type=None)
                    except Exception:
                        # Fallback: extract JSON substring
                        try:
                            idx = text.find('{')
                            return json.loads(text[idx:]) if idx >= 0 else {}
                        except Exception as e2:
                            logger.warning(f"_request: JSON fallback failed: {e2}; response text: {text[:100]}")
                            return {}
            except Exception as e:
                logger.warning(f"Attempt {attempt+1}/{max_retries} failed: {e}")
                await asyncio.sleep(1)
        raise RuntimeError(f"Failed request {method} {url}")

    def extract_hash_from_magnet(self, magnet: str) -> Optional[str]:
        m = re.search(r'btih:([A-Fa-f0-9]{40})', magnet)
        return m.group(1).lower() if m else None

    async def is_already_added(self, magnet: str, store_name: str, ip: Optional[str] = None) -> Optional[str]:
        h = self.extract_hash_from_magnet(magnet)
        if not h:
            return None
        # Liste les magnets existants et cherche l'ID associé au hash
        try:
            res = await self._request('GET', '/v0/store/magnets', store_name)
            items = res.get('data', {}).get('items', [])
            for item in items:
                if item.get('hash') == h:
                    return item.get('id')
            return None
        except aiohttp.ClientResponseError as e:
            # 404 ou 400 = non ajouté
            if e.status in (404, 400):
                return None
            raise

    async def add_magnet(self, magnet: str, store_name: str, ip: Optional[str] = None) -> str:
        h = self.extract_hash_from_magnet(magnet)
        if not h: raise ValueError("Invalid magnet")
        data = {'magnet': magnet}
        # Ajoute le magnet via /v0/store/magnets
        res = await self._request('POST', '/v0/store/magnets', store_name, json=data)
        logger.debug(f"StremThruDebrid.add_magnet raw response: {res}")
        # Extrait l'ID retourné (soit en top-level, soit sous 'data')
        id_ = None
        if isinstance(res, dict):
            id_ = res.get('id') or res.get('data', {}).get('id')
        return id_ or h

    async def get_torrent_info(self, torrent_id: str, store_name: str, ip: Optional[str] = None) -> Optional[Dict[str, Any]]:
        try:
            # Récupère le statut via /v0/store/magnets/{id}
            res = await self._request('GET', f'/v0/store/magnets/{torrent_id}', store_name)
            # Déroule le champ 'data'
            data = res.get('data') if isinstance(res, dict) else None
            return data if data and data.get('hash') else None
        except aiohttp.ClientResponseError as e:
            if e.status == 404:
                return None
            raise

    async def delete_torrent(self, torrent_id: str, store_name: str, ip: Optional[str] = None) -> bool:
        # Supprime le magnet par l'id StremThru
        await self._request('DELETE', f'/v0/store/magnets/{torrent_id}', store_name)
        return True

    async def unrestrict_link(self, link: str, store_name: str, ip: Optional[str] = None) -> Optional[str]:
        # Génère un lien direct via /v0/store/link/generate
        res = await self._request('POST', '/v0/store/link/generate', store_name, json={'link': link})
        # La réponse est dans data.link
        return res.get('data', {}).get('link')

    async def wait_for_link(self, torrent_id: str, store_name: str, timeout: int = 60, interval: int = 1) -> bool:
        start = time.time()
        while time.time()-start < timeout:
            info = await self.get_torrent_info(torrent_id, store_name)
            if info and info.get('status') in ['cached','ready','downloaded','completed']:
                return True
            await asyncio.sleep(interval)
        return False

    def _find_appropriate_link(self, files: List[Dict[str, Any]], file_index: Union[str,int], season: Optional[int], episode: Optional[int]) -> Tuple[Optional[str], Optional[int]]:
        # Gère file_index None ou invalide
        try:
            idx = int(file_index) if file_index is not None else -1
        except (TypeError, ValueError):
            idx = -1
        # Si pas de file_index valide, choisir le fichier avec lien et taille maximale
        if idx < 0:
            valid_files = [f for f in files if f.get('link')]
            if valid_files:
                largest = max(valid_files, key=lambda x: x.get('size', 0))
                return largest.get('link'), largest.get('index')
        for f in files:
            if f.get('index') == idx:
                return f.get('link'), idx
        return None, None

    async def get_stream_link(self, query: Dict[str, Any], config: Dict[str, Any], ip: Optional[str] = None) -> str:
        magnet = query['magnet']
        # Si aucun file_index fourni, on prend -1 (pour la vignettes ou premier lien)
        raw_idx = query.get('file_index')
        idx = raw_idx if raw_idx is not None else -1
        code_raw = query.get('store_code')
        if not code_raw:
            # Infer store_code from config debridDownloader
            default = config.get('debridDownloader')
            inv = {v: k for k, v in self.STORE_CODE_TO_NAME.items()}
            if default:
                code_raw = inv.get(default.lower())
                if code_raw:
                    logger.debug(f"get_stream_link: inferred store_code '{code_raw}' from config.debridDownloader")
                else:
                    logger.error("get_stream_link: cannot infer store_code from config.debridDownloader")
                    raise HTTPException(status_code=400, detail="Missing or invalid store_code")
            else:
                logger.error("get_stream_link: missing store_code and no debridDownloader in config")
                raise HTTPException(status_code=400, detail="Missing store_code")
        code = str(code_raw).lower()
        name = self.STORE_CODE_TO_NAME.get(code)
        if not name:
            logger.error(f"get_stream_link: invalid store_code '{code_raw}'")
            raise HTTPException(status_code=400, detail=f"Invalid store code: {code_raw}")
        # Check if magnet already added
        id_ = await self.is_already_added(magnet, name, ip)
        info: Optional[Dict[str, Any]] = None
        if id_:
            h = id_
            # Get initial torrent info
            info = await self.get_torrent_info(h, name, ip)
        else:
            # Add magnet and capture raw response to skip extra GET
            res = await self._request('POST', '/v0/store/magnets', name, json={'magnet': magnet})
            logger.debug(f"StremThruDebrid.add_magnet raw response: {res}")
            # Extract ID
            h = res.get('id') or res.get('data', {}).get('id') or self.extract_hash_from_magnet(magnet)
            # Use returned data if ready
            info = res.get('data') if isinstance(res, dict) else None
        # Wait for readiness if not already ready
        if not info or info.get('status') not in ['cached','ready','downloaded','completed']:
            if not await self.wait_for_link(h, name):
                raise RuntimeError("Timeout waiting for torrent")
            info = await self.get_torrent_info(h, name, ip)
        link, fid = self._find_appropriate_link(info.get('files',[]), idx, query.get('season'), query.get('episode'))
        if fid is None:
            raise RuntimeError("File not found")
        return await self.unrestrict_link(link, name, ip)

    def _find_appropriate_link(self, files: List[Dict[str, Any]], file_index: Union[str,int], season: Optional[int], episode: Optional[int]) -> Tuple[Optional[str], Optional[int]]:
        # Gère file_index None ou invalide
        try:
            idx = int(file_index) if file_index is not None else -1
        except (TypeError, ValueError):
            idx = -1
        # Si pas de file_index valide, choisir le fichier avec lien et taille maximale
        if idx < 0:
            valid_files = [f for f in files if f.get('link')]
            if valid_files:
                largest = max(valid_files, key=lambda x: x.get('size', 0))
                return largest.get('link'), largest.get('index')
        for f in files:
            if f.get('index') == idx:
                return f.get('link'), idx
        return None, None

    async def get_stream_link(self, query: Dict[str, Any], config: Dict[str, Any], ip: Optional[str] = None) -> str:
        magnet = query['magnet']
        # Si aucun file_index fourni, on prend -1 (pour la vignettes ou premier lien)
        raw_idx = query.get('file_index')
        idx = raw_idx if raw_idx is not None else -1
        code_raw = query.get('store_code')
        if not code_raw:
            # Infer store_code from config debridDownloader
            default = config.get('debridDownloader')
            inv = {v: k for k, v in self.STORE_CODE_TO_NAME.items()}
            if default:
                code_raw = inv.get(default.lower())
                if code_raw:
                    logger.debug(f"get_stream_link: inferred store_code '{code_raw}' from config.debridDownloader")
                else:
                    logger.error("get_stream_link: cannot infer store_code from config.debridDownloader")
                    raise HTTPException(status_code=400, detail="Missing or invalid store_code")
            else:
                logger.error("get_stream_link: missing store_code and no debridDownloader in config")
                raise HTTPException(status_code=400, detail="Missing store_code")
        code = str(code_raw).lower()
        name = self.STORE_CODE_TO_NAME.get(code)
        if not name:
            logger.error(f"get_stream_link: invalid store_code '{code_raw}'")
            raise HTTPException(status_code=400, detail=f"Invalid store code: {code_raw}")
        # Check if magnet already added
        id_ = await self.is_already_added(magnet, name, ip)
        info: Optional[Dict[str, Any]] = None
        if id_:
            h = id_
            # Get initial torrent info
            info = await self.get_torrent_info(h, name, ip)
        else:
            # Add magnet and capture raw response to skip extra GET
            res = await self._request('POST', '/v0/store/magnets', name, json={'magnet': magnet})
            logger.debug(f"StremThruDebrid.add_magnet raw response: {res}")
            # Extract ID
            h = res.get('id') or res.get('data', {}).get('id') or self.extract_hash_from_magnet(magnet)
            # Use returned data if ready
            info = res.get('data') if isinstance(res, dict) else None
        # Wait for readiness if not already ready
        if not info or info.get('status') not in ['cached','ready','downloaded','completed']:
            if not await self.wait_for_link(h, name):
                raise RuntimeError("Timeout waiting for torrent")
            info = await self.get_torrent_info(h, name, ip)
        link, fid = self._find_appropriate_link(info.get('files',[]), idx, query.get('season'), query.get('episode'))
        if fid is None:
            raise RuntimeError("File not found")
        return await self.unrestrict_link(link, name, ip)

    async def get_cached_files_async(self, torrent_items: List[Any], ip: Optional[str] = None, sid: Optional[str] = None) -> Tuple[Dict[str,List[Dict]], str]:
        return await self.get_cached_files(torrent_items, ip, sid)

    async def get_cached_files(self, torrent_items: List[Any], ip: Optional[str] = None, sid: Optional[str] = None) -> Tuple[Dict[str,List[Dict]], str]:
        hashes = [i.info_hash for i in torrent_items if getattr(i,'info_hash',None)]
        if not hashes: return {}, ''
        magnets = [f"magnet:?xt=urn:btih:{h}" for h in hashes]
        params={'magnet':','.join(magnets)}
        if sid:
            params['sid'] = sid
        # Select a store with available token, fallback to first
        store_name = next((sn for sn, tk in self.STORE_NAME_TO_TOKEN_KEY.items() if self.config.get(tk)), list(self.STORE_NAME_TO_TOKEN_KEY.keys())[0])
        # Use global check endpoint; store_name passed via header
        res = await self._request('GET', '/v0/store/magnets/check', store_name, params=params)
        items = res.get('data',{}).get('items',[])
        out={h:[] for h in hashes}
        for it in items:
            h = it.get('hash'); fs = it.get('files', [])
            for f in fs:
                # Use 'title' key to represent file name for consistency
                out[h].append({'file_index': f.get('index'), 'title': f.get('name'), 'size': f.get('size')})
        return out, store_name
