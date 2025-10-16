import os
import threading

from typing import List, Dict
from RTN import parse

from stream_fusion.utils.debrid.alldebrid import AllDebrid
from stream_fusion.utils.debrid.premiumize import Premiumize
from stream_fusion.utils.debrid.realdebrid import RealDebrid
from stream_fusion.utils.debrid.torbox import Torbox
from stream_fusion.utils.debrid.stremthru import StremThru
from stream_fusion.utils.torrent.torrent_item import TorrentItem
from stream_fusion.utils.cache.cache import cache_public
from stream_fusion.utils.general import season_episode_in_filename
from stream_fusion.logging_config import logger


class TorrentSmartContainer:
    def __init__(self, torrent_items: List[TorrentItem], media):
        self.logger = logger
        self.logger.info(
            f"Initializing TorrentSmartContainer with {len(torrent_items)} items"
        )
        self.__itemsDict: Dict[TorrentItem] = self._build_items_dict_by_infohash(
            torrent_items
        )
        self.__media = media
        # Variable d'utilisation de StremThru maintenue pour la rétrocompatibilité
        self.__using_stremthru = False
        # Log pour indiquer que tous les torrents sont inclus
        self.logger.info("TorrentSmartContainer: Including all torrents regardless of seeders count")

    def get_unaviable_hashes(self):
        hashes = []
        for hash, item in self.__itemsDict.items():
            if item.availability is False:
                hashes.append(hash)
        self.logger.debug(
            f"TorrentSmartContainer: Retrieved {len(hashes)} hashes to process"
        )
        return hashes

    def get_items(self):
        items = list(self.__itemsDict.values())
        self.logger.debug(f"TorrentSmartContainer: Retrieved {len(items)} items")
        return items

    def get_direct_torrentable(self):
        self.logger.info("TorrentSmartContainer: Retrieving direct torrentable items")
        direct_torrentable_items = []
        for torrent_item in self.__itemsDict.values():
            if torrent_item.privacy == "public" and torrent_item.file_index is not None:
                direct_torrentable_items.append(torrent_item)
        self.logger.info(
            f"TorrentSmartContainer: Found {len(direct_torrentable_items)} direct torrentable items"
        )
        return direct_torrentable_items

    def get_best_matching(self):
        self.logger.info("TorrentSmartContainer: Finding best matching items")
        best_matching = []
        self.logger.debug(
            f"TorrentSmartContainer: Total items to process: {len(self.__itemsDict)}"
        )
        for torrent_item in self.__itemsDict.values():
            self.logger.trace(
                f"TorrentSmartContainer: Processing item: {torrent_item.raw_title} - Has torrent: {torrent_item.torrent_download is not None}"
            )
            if torrent_item.torrent_download is not None:
                self.logger.trace(
                    f"TorrentSmartContainer: Has file index: {torrent_item.file_index is not None}"
                )
                if torrent_item.file_index is not None:
                    best_matching.append(torrent_item)
                    self.logger.trace(
                        "TorrentSmartContainer: Item added to best matching (has file index)"
                    )
                else:
                    matching_file = self._find_matching_file(
                        torrent_item.full_index,
                        self.__media.season,
                        self.__media.episode,
                    )
                    if matching_file:
                        torrent_item.file_index = matching_file["file_index"]
                        torrent_item.file_name = matching_file["file_name"]
                        torrent_item.size = matching_file["size"]
                        best_matching.append(torrent_item)
                        self.logger.trace(
                            f"TorrentSmartContainer: Item added to best matching (found matching file: {matching_file['file_name']})"
                        )
                    else:
                        self.logger.trace(
                            "TorrentSmartContainer: No matching file found, item not added to best matching"
                        )
            else:
                # Toujours inclure tous les torrents DMM - API et autres
                best_matching.append(torrent_item)
                seeders_info = f"with {torrent_item.seeders} seeders" if torrent_item.seeders is not None else "with unknown seeders"
                self.logger.trace(
                    f"TorrentSmartContainer: Item added to best matching (magnet link) - {seeders_info}"
                )
        self.logger.success(
            f"TorrentSmartContainer: Found {len(best_matching)} best matching items"
        )
        return best_matching

    def _find_matching_file(self, full_index, season, episode):
        self.logger.trace(
            f"TorrentSmartContainer: Searching for matching file: Season {season}, Episode {episode}"
        )

        if not full_index:
            self.logger.trace(
                "TorrentSmartContainer: Full index is empty, cannot find matching file"
            )
            return None
        try:
            target_season = int(season.replace("S", ""))
            target_episode = int(episode.replace("E", ""))
        except ValueError:
            self.logger.error(
                f"TorrentSmartContainer: Invalid season or episode format: {season}, {episode}"
            )
            return None

        best_match = None
        for file_entry in full_index:
            if (
                target_season in file_entry["seasons"]
                and target_episode in file_entry["episodes"]
            ):
                if best_match is None or file_entry["size"] > best_match["size"]:
                    best_match = file_entry
                    self.logger.trace(
                        f"TorrentSmartContainer: Found potential match: {file_entry['file_name']}"
                    )

        if best_match:
            self.logger.trace(
                f"TorrentSmartContainer: Best matching file found: {best_match['file_name']}"
            )
            return best_match
        else:
            self.logger.warning(
                f"TorrentSmartContainer: No matching file found for Season {season}, Episode {episode}"
            )
            return None

    def cache_container_items(self):
        self.logger.info(
            "TorrentSmartContainer: Starting cache process for container items"
        )
        threading.Thread(target=self._save_to_cache).start()

    def _save_to_cache(self):
        self.logger.info("TorrentSmartContainer: Saving public items to cache")
        public_torrents = list(
            filter(lambda x: x.privacy == "public", self.get_items())
        )
        self.logger.debug(
            f"TorrentSmartContainer: Found {len(public_torrents)} public torrents to cache"
        )
        cache_public(public_torrents, self.__media)
        self.logger.info("TorrentSmartContainer: Caching process completed")

    def update_availability(self, debrid_response, debrid_type, media):
        if not debrid_response or debrid_response == {} or debrid_response == []:
            self.logger.debug(
                "TorrentSmartContainer: Debrid response is empty : "
                + str(debrid_response)
            )
            return
        self.logger.info(
            f"TorrentSmartContainer: Updating availability for {debrid_type.__name__}"
        )
        if debrid_type is RealDebrid:
            self._update_availability_realdebrid(debrid_response, media)
        elif debrid_type is AllDebrid:
            self._update_availability_alldebrid(debrid_response, media)
        elif debrid_type is Torbox:
            self._update_availability_torbox(debrid_response, media)
        elif debrid_type is Premiumize:
            self._update_availability_premiumize(debrid_response)
        elif debrid_type is StremThru or debrid_type.__name__ == "StremThru":
            # Marquer que StremThru est utilisé
            self.__using_stremthru = True
            
            # Récupérer l'instance depuis le tableau de debrid_response
            if debrid_response and isinstance(debrid_response[0], dict) and "store_name" in debrid_response[0]:
                store_name = debrid_response[0]["store_name"]
            else:
                # Tenter de récupérer depuis le Logger
                try:
                    log_entries = [line for line in self.logger.get_entries() if "StremThru: Vérification de" in line and "magnets sur StremThru-" in line]
                    if log_entries:
                        latest_log = log_entries[-1]
                        store_name = latest_log.split("StremThru-")[-1].strip()
                    else:
                        # Fallback sur les stores courants si on ne peut pas détecter
                        store_name = "torbox" if "TBToken" in str(debrid_response) else "alldebrid"
                except:
                    # Fallback sur "torbox" s'il y a TB dans les logs
                    store_name = "torbox" if "TBToken" in str(debrid_response) else "alldebrid"
            
            underlying_debrid = StremThru.get_underlying_debrid_code(store_name)
            self.logger.debug(f"TorrentSmartContainer: StremThru utilise le store: {store_name}, code: {underlying_debrid}")
            self._update_availability_stremthru(debrid_response, media, underlying_debrid)
        else:
            self.logger.error(
                f"TorrentSmartContainer: Unsupported debrid type: {debrid_type.__name__}"
            )
            raise NotImplementedError(
                f"TorrentSmartContainer: Debrid type {debrid_type.__name__} not implemented"
            )

    def _update_availability_realdebrid(self, response, media):
        self.logger.info("TorrentSmartContainer: Updating availability for RealDebrid")
        for info_hash, details in response.items():
            if "rd" not in details:
                self.logger.debug(
                    f"TorrentSmartContainer: Skipping hash {info_hash}: no RealDebrid data"
                )
                continue
            torrent_item: TorrentItem = self.__itemsDict[info_hash]
            self.logger.debug(
                f"Processing {torrent_item.type}: {torrent_item.raw_title}"
            )
            files = []
            if torrent_item.type == "series":
                self._process_series_files(
                    details, media, torrent_item, files, debrid="RD"
                )
            else:
                self._process_movie_files(details, files)
            self._update_file_details(torrent_item, files, debrid="RD")
        self.logger.info(
            "TorrentSmartContainer: RealDebrid availability update completed"
        )

    def _process_series_files(
        self, details, media, torrent_item, files, debrid: str = "??"
    ):
        for variants in details["rd"]:
            file_found = False
            for file_index, file in variants.items():
                clean_season = media.season.replace("S", "")
                clean_episode = media.episode.replace("E", "")
                numeric_season = int(clean_season)
                numeric_episode = int(clean_episode)
                if season_episode_in_filename(
                    file["filename"], numeric_season, numeric_episode
                ):
                    self.logger.debug(f"Matching file found: {file['filename']}")
                    torrent_item.file_index = file_index
                    torrent_item.file_name = file["filename"]
                    torrent_item.size = file["filesize"]
                    torrent_item.availability = debrid
                    file_found = True
                    files.append(
                        {
                            "file_index": file_index,
                            "title": file["filename"],
                            "size": file["filesize"],
                        }
                    )
                    break
            if file_found:
                break

    def _process_movie_files(self, details, files):
        for variants in details["rd"]:
            for file_index, file in variants.items():
                self.logger.debug(
                    f"TorrentSmartContainer: Adding movie file: {file['filename']}"
                )
                files.append(
                    {
                        "file_index": file_index,
                        "title": file["filename"],
                        "size": file["filesize"],
                    }
                )

    def _update_availability_alldebrid(self, response, media):
        self.logger.info("TorrentSmartContainer: Updating availability for AllDebrid")
        if not response["status"] == "success":
            self.logger.error(f"TorrentSmartContainer: AllDebrid API error: {response}")
            return

        for data in response["data"]["magnets"]:
            torrent_item: TorrentItem = self.__itemsDict[data["hash"]]
            
            # Set availability to AD immediately for all files
            torrent_item.availability = "AD"
            
            # Process files if they exist
            if "files" in data and data["files"]:
                files = []
                self._explore_folders_alldebrid(
                    data["files"], files, 1, torrent_item.type, media
                )
                if files:  # If we found matching files
                    self._update_file_details(torrent_item, files, debrid="AD")
            else:
                # If no files data, still mark as available
                self.logger.debug(f"No files data for hash {data['hash']}, but marking as available")
                torrent_item.availability = "AD"
                
        self.logger.info(
            "TorrentSmartContainer: AllDebrid availability update completed"
        )

    def _update_availability_torbox(self, response, media):
        self.logger.info("TorrentSmartContainer: Updating availability for Torbox")
        if response["success"] is False:
            self.logger.error(f"TorrentSmartContainer: Torbox API error: {response}")
            return

        for data in response["data"]:
            torrent_item: TorrentItem = self.__itemsDict[data["hash"]]
            files = self._process_torbox_files(data["files"], torrent_item.type, media)
            self._update_file_details(torrent_item, files, debrid="TB")

        self.logger.info("TorrentSmartContainer: Torbox availability update completed")

    def _process_torbox_files(self, files, type, media):
        processed_files = []
        for index, file in enumerate(files):
            if type == "series":
                if self._is_matching_episode_torbox(file["name"], media):
                    processed_files.append(
                        {
                            "file_index": index,
                            "title": file["name"],
                            "size": file["size"],
                        }
                    )
            elif type == "movie":
                processed_files.append(
                    {
                        "file_index": index,
                        "title": file["name"],
                        "size": file["size"],
                    }
                )
        return processed_files

    def _is_matching_episode_torbox(self, filepath, media):
            # Extract only the filename from the full path
            filename = os.path.basename(filepath)
            
            clean_season = media.season.replace("S", "")
            clean_episode = media.episode.replace("E", "")
            numeric_season = int(clean_season)
            numeric_episode = int(clean_episode)
            
            return season_episode_in_filename(filename, numeric_season, numeric_episode)

    def _update_availability_premiumize(self, response):
        self.logger.info("TorrentSmartContainer: Updating availability for Premiumize")
        if not response:
            self.logger.error(
                f"TorrentSmartContainer: Empty response from Premiumize API"
            )
            return

        torrent_items = self.get_items()
        for hash, status in response.items():
            for item in torrent_items:
                if item.info_hash.lower() == hash.lower():
                    is_available = status.get("transcoded", False)
                    item.availability = "PM" if is_available else None
                    
                    if is_available:
                        if item.type == "series":
                            # Pour les séries, vérifier si le fichier sélectionné correspond à l'épisode
                            if "full_index" in item.__dict__ and item.full_index:
                                # Si nous avons l'index complet des fichiers, l'utiliser
                                matching_files = []
                                for file_info in item.full_index:
                                    clean_season = self.__media.season.replace("S", "")
                                    clean_episode = self.__media.episode.replace("E", "")
                                    numeric_season = int(clean_season)
                                    numeric_episode = int(clean_episode)
                                    
                                    if (numeric_season in file_info.get("seasons", []) and 
                                        numeric_episode in file_info.get("episodes", [])):
                                        matched_file_info = {
                                            "file_index": file_info.get("file_index", 0),
                                            "title": file_info.get("file_name", ""),
                                            "size": file_info.get("size", 0),
                                        }
                                        self._update_file_details(item, [matched_file_info], debrid="PM")
                                        self.logger.debug(
                                            f"TorrentSmartContainer: Updated series file details for {item.raw_title}: {matched_file_info}"
                                        )
                                        break
                        elif item.type == "movie":
                            # Process movie files
                            self.logger.debug(
                                f"TorrentSmartContainer: Processing movie files for {item.raw_title}"
                            )
                            
                            # Vérifier si nous avons des informations sur le fichier dans le status
                            file_info = None
                            
                            # Si nous avons des fichiers dans le status
                            if "files" in status:
                                cached_files = [
                                    f for f in status["files"] if f.get("cached", False) is True
                                ]
                                if cached_files:
                                    # Find the largest cached file
                                    largest_file = max(
                                        cached_files, key=lambda f: f.get("size", 0)
                                    )
                                    file_info = {
                                        "file_index": largest_file.get("file_index", 0),
                                        "title": largest_file.get("title", ""),
                                        "size": largest_file.get("size", 0),
                                    }
                            
                            # Si nous n'avons pas d'infos de fichiers mais un nom de fichier et une taille
                            if not file_info and "filename" in status and "filesize" in status:
                                file_info = {
                                    "file_index": 0,
                                    "title": status.get("filename", ""),
                                    "size": int(status.get("filesize", 0)),
                                }
                            
                            # Si nous avons des informations sur le fichier, mettre à jour
                            if file_info:
                                self._update_file_details(item, [file_info], debrid="PM")
                                self.logger.debug(
                                    f"TorrentSmartContainer: Updated movie file details for {item.raw_title}: {file_info}"
                                )

        self.logger.info(
            "TorrentSmartContainer: Premiumize availability update completed"
        )

    def _update_availability_stremthru(self, response, media, underlying_debrid="AD"):
        self.logger.info(f"TorrentSmartContainer: Updating StremThru availability (via {underlying_debrid})")
        for result in response:
            hash_value = result.get("hash", "").lower()
            
            # Utiliser le code debrid fourni par StremThru si disponible
            result_debrid = result.get("debrid")
            if result_debrid:
                debrid_code = result_debrid
                self.logger.debug(f"TorrentSmartContainer: Utilisation du code debrid spécifique: {debrid_code} pour {hash_value}")
            else:
                debrid_code = underlying_debrid
            
            if hash_value in self.__itemsDict:
                item = self.__itemsDict[hash_value]
                item.availability = debrid_code  # Utiliser le code du service spécifique à ce résultat
                
                # Récupérer les fichiers du torrent
                files = result.get("files", [])
                
                # Détecter si c'est un pack de saison (plus de 5 fichiers vidéo)
                video_files = [f for f in files if f.get("name", "").lower().endswith((".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm"))]
                is_season_pack = item.type == "series" and len(video_files) > 5
                
                if is_season_pack:
                    self.logger.info(f"TorrentSmartContainer: Détection d'un pack de saison avec {len(video_files)} fichiers vidéo pour {item.raw_title}")
                
                if item.type == "series":
                    # Version originale simple : chercher les fichiers qui correspondent à l'épisode
                    self.logger.debug(
                        f"TorrentSmartContainer: Processing series files for {item.raw_title}"
                    )
                    matching_files = []
                    clean_season = media.season.replace("S", "")
                    clean_episode = media.episode.replace("E", "")
                    numeric_season = int(clean_season)
                    numeric_episode = int(clean_episode)
                    
                    for file in files:
                        file_name = file.get("name", "")
                        if season_episode_in_filename(file_name, numeric_season, numeric_episode):
                            file_info = {
                                "file_index": file.get("index", 0),
                                "title": file_name,
                                "size": file.get("size", 0),
                            }
                            matching_files.append(file_info)
                            self.logger.debug(f"TorrentSmartContainer: Match found: {file_name}")
                    
                    if matching_files:
                        self._update_file_details(item, matching_files, debrid=debrid_code)
                    else:
                        self.logger.debug(f"TorrentSmartContainer: No matching episode files found for {item.raw_title}")
                        from stream_fusion.utils.general import smart_episode_fallback
                        
                        fallback_file = smart_episode_fallback(files, numeric_season, numeric_episode)
                        if fallback_file:
                            file_info = {
                                "file_index": fallback_file.get("index", 0),
                                "title": fallback_file.get("name", ""),
                                "size": fallback_file.get("size", 0),
                            }
                            self._update_file_details(item, [file_info], debrid=debrid_code)
                            self.logger.info(f"TorrentSmartContainer: Fallback intelligent utilisé pour {item.raw_title}: {fallback_file.get('name')}")
                        else:
                            # Vraiment aucun fichier trouvé - marquer comme disponible mais sans file_index
                            item.availability = debrid_code
                        
                elif item.type == "movie":
                    self.logger.debug(
                        f"TorrentSmartContainer: Processing movie files for {item.raw_title}"
                    )
                    
                    if files:
                        file_infos = [
                            {
                                "file_index": file.get("index", 0),
                                "title": file.get("name", ""),
                                "size": file.get("size", 0),
                            }
                            for file in files
                        ]
                        self._update_file_details(item, file_infos, debrid=debrid_code)
                        self.logger.debug(
                            f"TorrentSmartContainer: Updated movie file details for {item.raw_title}"
                        )
                
                self.logger.debug(
                    f"TorrentSmartContainer: Updated availability for {item.raw_title}: {item.availability}"
                )
        
        self.logger.info(
            "TorrentSmartContainer: StremThru availability update completed"
        )

    def _update_file_details(self, torrent_item, files, debrid: str = "??"):
        if not files:
            self.logger.debug(
                f"TorrentSmartContainer: No files to update for {torrent_item.raw_title}"
            )
            return
        file = max(files, key=lambda file: file["size"])
        torrent_item.availability = debrid
        # Only update file_index if it wasn't set to None for intelligent selection
        if torrent_item.file_index is not None:
            torrent_item.file_index = file["file_index"]
        torrent_item.file_name = file["title"]
        torrent_item.size = file["size"]
        self.logger.debug(
            f"TorrentSmartContainer: Updated file details for {torrent_item.raw_title}: {file['title']}"
        )

    def _build_items_dict_by_infohash(self, items: List[TorrentItem]):
        self.logger.info(
            f"TorrentSmartContainer: Building items dictionary by infohash ({len(items)} items)"
        )
        items_dict = {}
        for item in items:
            if item.info_hash is not None:
                if item.info_hash not in items_dict:
                    self.logger.debug(f"Adding {item.info_hash} to items dict")
                    items_dict[item.info_hash] = item
                else:
                    self.logger.debug(
                        f"TorrentSmartContainer: Skipping duplicate info hash: {item.info_hash}"
                    )
        self.logger.info(
            f"TorrentSmartContainer: Built dictionary with {len(items_dict)} unique items"
        )
        return items_dict

    def _explore_folders_alldebrid(self, folder, files, file_index, type, media):

        if type == "series":
            for file in folder:
                if "e" in file:
                    file_index = self._explore_folders_alldebrid(
                        file["e"], files, file_index, type, media
                    )
                    continue
                parsed_file = parse(file["n"])
                clean_season = media.season.replace("S", "")
                clean_episode = media.episode.replace("E", "")
                numeric_season = int(clean_season)
                numeric_episode = int(clean_episode)
                if (
                    numeric_season in parsed_file.seasons
                    and numeric_episode in parsed_file.episodes
                ):
                    self.logger.debug(
                        f"TorrentSmartContainer: Matching series file found: {file['n']}"
                    )
                    files.append(
                        {
                            "file_index": file_index,
                            "title": file["n"],
                            "size": file["s"] if "s" in file else 0,
                        }
                    )
                file_index += 1
        elif type == "movie":
            file_index = 1
            for file in folder:
                if "e" in file:
                    file_index = self._explore_folders_alldebrid(
                        file["e"], files, file_index, type, media
                    )
                    continue
                self.logger.debug(
                    f"TorrentSmartContainer: Adding movie file: {file['n']}"
                )
                files.append(
                    {
                        "file_index": file_index,
                        "title": file["n"],
                        "size": file["s"] if "s" in file else 0,
                    }
                )
                file_index += 1
        return file_index
