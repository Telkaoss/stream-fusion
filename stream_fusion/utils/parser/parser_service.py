import json
import queue
import threading
from typing import List, Dict

from RTN import ParsedData
from stream_fusion.settings import settings
from stream_fusion.utils.models.media import Media
from stream_fusion.utils.torrent.torrent_item import TorrentItem
from stream_fusion.utils.string_encoding import encodeb64

from stream_fusion.utils.parser.parser_utils import (
    detect_french_language,
    extract_release_group,
    filter_by_availability,
    filter_by_direct_torrent,
    get_emoji,
    INSTANTLY_AVAILABLE,
    DOWNLOAD_REQUIRED,
    DIRECT_TORRENT,
)


class StreamParser:
    def __init__(self, config: Dict):
        self.config = config
        self.configb64 = encodeb64(json.dumps(config).replace("=", "%3D"))

    def parse_to_stremio_streams(
        self, torrent_items: List[TorrentItem], media: Media
    ) -> List[Dict]:
        stream_list = []
        threads = []
        thread_results_queue = queue.Queue()

        for torrent_item in torrent_items[: int(self.config["maxResults"])]:
            thread = threading.Thread(
                target=self._parse_to_debrid_stream,
                args=(torrent_item, thread_results_queue, media),
                daemon=True,
            )
            thread.start()
            threads.append(thread)

        for thread in threads:
            thread.join()

        while not thread_results_queue.empty():
            stream_list.append(thread_results_queue.get())

        if self.config["debrid"]:
            stream_list = sorted(stream_list, key=filter_by_availability)
            stream_list = sorted(stream_list, key=filter_by_direct_torrent)

        return stream_list

    def _generate_binge_group(self, torrent_item: TorrentItem, media: Media) -> str:
        """Génère un bingeGroup intelligent selon le type de média"""
        
        if media.type == "movie":
            return f"stream-fusion-{torrent_item.info_hash}"
        
        if media.type == "series":
            series_id = media.id.split(":")[0] if ":" in media.id else media.id
            if torrent_item.parsed_data.quality:
                if isinstance(torrent_item.parsed_data.quality, list):
                    quality = torrent_item.parsed_data.quality[0] if torrent_item.parsed_data.quality else "Unknown"
                else:
                    quality = torrent_item.parsed_data.quality 
                quality = "Unknown"
            debrid = torrent_item.availability or "DL"
            binge_group = f"stream-fusion-{series_id}-{quality}-{debrid}"
            
            return binge_group
        
        # Fallback
        return f"stream-fusion-{torrent_item.info_hash}"



    def _parse_to_debrid_stream(
        self, torrent_item: TorrentItem, results: queue.Queue, media: Media
    ) -> None:
        parsed_data: ParsedData = torrent_item.parsed_data
        name = self._create_stream_name(torrent_item, parsed_data)
        title = self._create_stream_title(torrent_item, parsed_data, media)

        queryb64 = encodeb64(
            json.dumps(torrent_item.to_debrid_stream_query(media))
        ).replace("=", "%3D")

        results.put(
            {
                "name": name,
                "description": title,
                "url": f"{self.config['addonHost']}/playback/{self.configb64}/{queryb64}",
                "behaviorHints": {
                    "bingeGroup": self._generate_binge_group(torrent_item, media),
                    "filename": torrent_item.file_name or torrent_item.raw_title,
                },
            }
        )

        if self.config["torrenting"] and torrent_item.privacy == "public":
            self._add_direct_torrent_stream(torrent_item, parsed_data, title, results, media)

    def _create_stream_name(
        self, torrent_item: TorrentItem, parsed_data: ParsedData
    ) -> str:
        resolution = parsed_data.resolution or "Unknown"
        # Services de debrid principaux
        if torrent_item.availability == "RD":
            name = f"{INSTANTLY_AVAILABLE}instant\nReal-Debrid\n({resolution})"
        elif torrent_item.availability == "AD":
            name = f"{INSTANTLY_AVAILABLE}instant\nAllDebrid\n({resolution})"
        elif torrent_item.availability == "TB":
            name = f"{INSTANTLY_AVAILABLE}instant\nTorBox\n({resolution})"
        elif torrent_item.availability == "PM":
            name = f"{INSTANTLY_AVAILABLE}instant\nPremiumize\n({resolution})"
        # Services de debrid additionnels
        elif torrent_item.availability == "OC":
            name = f"{INSTANTLY_AVAILABLE}instant\nOffcloud\n({resolution})"
        elif torrent_item.availability == "DL":
            name = f"{INSTANTLY_AVAILABLE}instant\nDebridLink\n({resolution})"
        elif torrent_item.availability == "ED":
            name = f"{INSTANTLY_AVAILABLE}instant\nEasyDebrid\n({resolution})"
        elif torrent_item.availability == "PK":
            name = f"{INSTANTLY_AVAILABLE}instant\nPikPak\n({resolution})"
        else:
            name = f"{DOWNLOAD_REQUIRED}download\n{self.config.get("debridDownloader", settings.download_service)}\n({resolution})"
        return name

    def _create_stream_title(
        self, torrent_item: TorrentItem, parsed_data: ParsedData, media: Media
    ) -> str:
        title = f"{torrent_item.raw_title}\n"

        if media.type == "series" and torrent_item.file_name:
            title += f"{torrent_item.file_name}\n"

        title += self._add_language_info(torrent_item, parsed_data)
        title += self._add_torrent_info(torrent_item)
        title += self._add_media_info(parsed_data)

        return title.strip()

    def _add_language_info(
        self, torrent_item: TorrentItem, parsed_data: ParsedData
    ) -> str:
        info = (
            "/".join(get_emoji(lang) for lang in torrent_item.languages)
            if torrent_item.languages
            else "🌐"
        )

        lang_type = detect_french_language(torrent_item.raw_title)
        if lang_type:
            info += f"  ✔ {lang_type} "

        group = extract_release_group(torrent_item.raw_title) or parsed_data.group
        if group:
            info += f"  ☠️ {group}"

        return f"{info}\n"

    def _add_torrent_info(self, torrent_item: TorrentItem) -> str:
        size_in_gb = round(int(torrent_item.size) / 1024 / 1024 / 1024, 2)
        return f"🔍 {torrent_item.indexer} 💾 {size_in_gb}GB 👥 {torrent_item.seeders} \n"

    def _add_media_info(self, parsed_data: ParsedData) -> str:
        info = []
        if parsed_data.codec:
            info.append(f"🎥 {parsed_data.codec}")
        if parsed_data.quality:
            info.append(f"📺 {parsed_data.quality}")
        if parsed_data.audio:
            info.append(f"🎧 {' '.join(parsed_data.audio)}")
        return " ".join(info) + "\n" if info else ""

    def _add_direct_torrent_stream(
        self,
        torrent_item: TorrentItem,
        parsed_data: ParsedData,
        title: str,
        results: queue.Queue,
        media: Media,
    ) -> None:
        direct_torrent_name = f"{DIRECT_TORRENT}\n{parsed_data.quality}\n"
        if parsed_data.quality and parsed_data.quality[0] not in ["Unknown", ""]:
            direct_torrent_name += f"({'|'.join(parsed_data.quality)})"

        results.put(
            {
                "name": direct_torrent_name,
                "description": title,
                "infoHash": torrent_item.info_hash,
                "fileIdx": (
                    int(torrent_item.file_index) if torrent_item.file_index else None
                ),
                "behaviorHints": {
                    "bingeGroup": self._generate_binge_group(torrent_item, media),
                    "filename": torrent_item.file_name or torrent_item.raw_title,
                },
            }
        )
