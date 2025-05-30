import hashlib
import os
import time
import urllib.parse
from typing import List

import bencode
import requests
from RTN import parse

from stream_fusion.services.postgresql.dao.torrentitem_dao import TorrentItemDAO
from stream_fusion.utils.jackett.jackett_result import JackettResult
from stream_fusion.utils.sharewood.sharewood_result import SharewoodResult
from stream_fusion.utils.zilean.zilean_result import ZileanResult
from stream_fusion.utils.yggfilx.yggflix_result import YggflixResult
from stream_fusion.utils.torrent.torrent_item import TorrentItem
from stream_fusion.utils.general import get_info_hash_from_magnet
from stream_fusion.logging_config import logger
from stream_fusion.settings import settings

class TorrentService:
    def __init__(self, config, torrent_dao: TorrentItemDAO):
        self.config = config
        self.torrent_dao = torrent_dao
        self.logger = logger
        self.__session = requests.Session()

    @staticmethod
    def __generate_unique_id(raw_title: str, indexer: str = "cached") -> str:
        unique_string = f"{raw_title}_{indexer}"
        full_hash = hashlib.sha256(unique_string.encode()).hexdigest()
        return full_hash[:16]

    async def get_cached_torrent(self, raw_title: str, indexer: str) -> TorrentItem | None:
        unique_id = self.__generate_unique_id(raw_title, indexer)
        try:
            cached_item = await self.torrent_dao.get_torrent_item_by_id(unique_id)
            if cached_item:
                return cached_item.to_torrent_item()
            return None
        except Exception as e:
            self.logger.error(f"Error getting cached torrent: {e}")
            return None

    async def cache_torrent(self, torrent_item: TorrentItem, id: str = None):
        unique_id = self.__generate_unique_id(torrent_item.raw_title, torrent_item.indexer)
        await self.torrent_dao.create_torrent_item(torrent_item, unique_id)

    async def convert_and_process(self, results: List[JackettResult | ZileanResult | YggflixResult | SharewoodResult]):
        torrent_items_result = []

        for result in results:
            torrent_item = result.convert_to_torrent_item()

            cached_item = await self.get_cached_torrent(torrent_item.raw_title, torrent_item.indexer)
            if cached_item:
                torrent_items_result.append(cached_item)
                continue

            if torrent_item.link.startswith("magnet:"):
                processed_torrent_item = self.__process_magnet(torrent_item)
            elif settings.sharewood_url and torrent_item.link.startswith(settings.sharewood_url):
                processed_torrent_item = self.__process_sharewood_web_url(torrent_item)
            elif settings.yggflix_url and torrent_item.link.startswith(settings.yggflix_url):
                processed_torrent_item = self.__process_ygg_api_url(torrent_item)
            else:
                processed_torrent_item = self.__process_web_url(torrent_item)

            await self.cache_torrent(processed_torrent_item)
            torrent_items_result.append(processed_torrent_item)

        return torrent_items_result
        
    def __process_sharewood_web_url(self, result: TorrentItem):
        if not self.config["sharewood"]:
            logger.error("Sharewood is not enabled in the config. Skipping processing of Sharewood URL.")
        
        try:
            time.sleep(1) # API limit 1 request per second
            response = self.__session.get(result.link, allow_redirects=True, timeout=5)
        except requests.exceptions.RequestException:
            self.logger.error(f"Error while processing url: {result.link}")
            return result
        except requests.exceptions.ReadTimeout:
            self.logger.error(f"Timeout while processing url: {result.link}")
            return result
        
        if response.status_code == 200:
            return self.__process_torrent(result, response.content)
        else:
            self.logger.error(f"Error code {response.status_code} while processing sharewood url: {result.link}")

        return result


    def __process_ygg_api_url(self, result: TorrentItem): 
        if not self.config["yggflix"]:
            logger.error("Yggflix is not enabled in the config. Skipping processing of Yggflix URL.")
        try:
            response = self.__session.get(result.link, timeout=10)
            time.sleep(0.1) # Add a delay of 0.1 seconds between requests faire usage for small VPS
        except requests.exceptions.RequestException:
            self.logger.error(f"Error while processing url: {result.link}")
            return result
        except requests.exceptions.ReadTimeout:
            self.logger.error(f"Timeout while processing url: {result.link}")
            return result
        
        if response.status_code == 200:
            return self.__process_torrent(result, response.content)
        elif response.status_code == 422:
            self.logger.info(f"Not aviable torrent on yggflix: {result.file_name}")
        else:
            self.logger.error(f"Error code {response.status_code} while processing ygg url: {result.link}")

        return result

    def __process_web_url(self, result: TorrentItem):
        try:
            time.sleep(0.2)
            response = self.__session.get(result.link, allow_redirects=False, timeout=40) # flaresolverr and Jackett timeouts
        except requests.exceptions.RequestException:
            self.logger.error(f"Error while processing url: {result.link}")
            return result
        except requests.exceptions.ReadTimeout:
            self.logger.error(f"Timeout while processing url: {result.link}")
            return result

        if response.status_code == 200:
            return self.__process_torrent(result, response.content)
        elif response.status_code == 302:
            result.magnet = response.headers['Location']
            return self.__process_magnet(result)
        else:
            self.logger.error(f"Error code {response.status_code} while processing url: {result.link}")

        return result

    def __process_torrent(self, result: TorrentItem, torrent_file):
        try:
            metadata = bencode.bdecode(torrent_file)
        except Exception as e:
            try:
                from bencodepy import Decoder
                decoder = Decoder(encoding='latin-1') 
                metadata = decoder.decode(torrent_file)
            except Exception as inner_e:
                logger.error(f"Impossible de décoder le fichier torrent: {str(e)} puis {str(inner_e)}")
                result.torrent_download = result.link
                result.trackers = []
                result.info_hash = ""
                result.magnet = ""
                return result

        result.torrent_download = result.link
        try:
            result.trackers = self.__get_trackers_from_torrent(metadata)
            result.info_hash = self.__convert_torrent_to_hash(metadata["info"])
            result.magnet = self.__build_magnet(result.info_hash, metadata["info"]["name"], result.trackers)
        except Exception as e:
            logger.error(f"Erreur lors du traitement des métadonnées du torrent: {str(e)}")
            result.trackers = []
            result.info_hash = ""
            result.magnet = ""

        if "files" not in metadata["info"]:
            result.file_index = 1
            return result

        result.files = metadata["info"]["files"]

        if result.type == "series":
            file_details = self.__find_single_episode_file(result.files, result.parsed_data.seasons, result.parsed_data.episodes)

            if file_details is not None:
                self.logger.debug("File details")
                self.logger.debug(file_details)
                result.file_index = file_details["file_index"]
                result.file_name = file_details["title"]
                result.size = file_details["size"]
            else:
                result.full_index = self.__find_full_index(result.files)

        if result.type == "movie":
            result.file_index = self.__find_movie_file(result.files)

        return result

    def __process_magnet(self, result: TorrentItem):
        if result.magnet is None:
            result.magnet = result.link

        if result.info_hash is None:
            result.info_hash = get_info_hash_from_magnet(result.magnet)

        result.trackers = self.__get_trackers_from_magnet(result.magnet)

        return result

    def __convert_torrent_to_hash(self, torrent_contents):
        hashcontents = bencode.bencode(torrent_contents)
        hexHash = hashlib.sha1(hashcontents).hexdigest()
        return hexHash.lower()

    def __build_magnet(self, hash, display_name, trackers):
        magnet_base = "magnet:?xt=urn:btih:"
        magnet = f"{magnet_base}{hash}&dn={display_name}"

        if len(trackers) > 0:
            magnet = f"{magnet}&tr={'&tr='.join(trackers)}"

        return magnet

    def __get_trackers_from_torrent(self, torrent_metadata):
        # Sometimes list, sometimes string
        announce = torrent_metadata["announce"] if "announce" in torrent_metadata else []
        # Sometimes 2D array, sometimes 1D array
        announce_list = torrent_metadata["announce-list"] if "announce-list" in torrent_metadata else []

        trackers = set()
        if isinstance(announce, str):
            trackers.add(announce)
        elif isinstance(announce, list):
            for tracker in announce:
                trackers.add(tracker)

        for announce_list_item in announce_list:
            if isinstance(announce_list_item, list):
                for tracker in announce_list_item:
                    trackers.add(tracker)
            if isinstance(announce_list_item, str):
                trackers.add(announce_list_item)

        return list(trackers)

    def __get_trackers_from_magnet(self, magnet: str):
        url_parts = urllib.parse.urlparse(magnet)
        query_parts = urllib.parse.parse_qs(url_parts.query)

        trackers = []
        if "tr" in query_parts:
            trackers = query_parts["tr"]

        return trackers

    def __find_single_episode_file(self, file_structure, season, episode):

        if len(season) == 0 or len(episode) == 0:
            return None

        file_index = 1
        strict_episode_files = []
        episode_files = []
        for files in file_structure:
            for file in files["path"]:

                parsed_file = parse(file)

                if season[0] in parsed_file.seasons and episode[0] in parsed_file.episodes:
                    episode_files.append({
                        "file_index": file_index,
                        "title": file,
                        "size": files["length"]
                    })

            # Doesn't that need to be indented?
            file_index += 1

        return max(episode_files, key=lambda file: file["size"])
    
    def __find_full_index(self, file_structure):
        self.logger.debug("Starting to build full index of video files")
        video_formats = {".mkv", ".mp4", ".avi", ".mov", ".flv", ".wmv", ".webm", ".mpg", ".mpeg", ".m4v", ".3gp", ".3g2",
                        ".ogv", ".ogg", ".drc", ".gif", ".gifv", ".mng", ".avi", ".mov", ".qt", ".wmv", ".yuv", ".rm",
                        ".rmvb", ".asf", ".amv", ".m4p", ".m4v", ".mpg", ".mp2", ".mpeg", ".mpe", ".mpv", ".mpg",
                        ".mpeg", ".m2v", ".m4v", ".svi", ".3gp", ".3g2", ".mxf", ".roq", ".nsv", ".flv", ".f4v",
                        ".f4p", ".f4a", ".f4b"}
        
        full_index = []
        file_index = 1

        for file_entry in file_structure:
            file_path = file_entry.get("path", [])
            if isinstance(file_path, list):
                file_name = file_path[-1] if file_path else ""
            else:
                file_name = file_path

            _, file_extension = os.path.splitext(file_name.lower())
            
            if file_extension in video_formats:
                parsed_file = parse(file_name)
                if len(parsed_file.seasons) == 0 or len(parsed_file.episodes) == 0:
                    self.logger.debug(f"Skipping file without season or episode parsed: {file_name}")
                    continue
                full_index.append({
                    "file_index": file_index,
                    "file_name": file_name,
                    "full_path": os.path.join(*file_path) if isinstance(file_path, list) else file_path,
                    "size": file_entry.get("length", 0),
                    "seasons": parsed_file.seasons,
                    "episodes": parsed_file.episodes
                })
                self.logger.trace(f"Added file to index: {file_name}")
            
            file_index += 1
        
        self.logger.debug(f"Full index built with {len(full_index)} video files")
        return full_index

    def __find_movie_file(self, file_structure):
        max_size = 0
        max_file_index = 1
        current_file_index = 1
        for files in file_structure:
            if files["length"] > max_size:
                max_file_index = current_file_index
                max_size = files["length"]
            current_file_index += 1

        return max_file_index
