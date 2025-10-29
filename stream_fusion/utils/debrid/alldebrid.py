# alldebrid.py
import uuid
from urllib.parse import unquote

from fastapi import HTTPException

from stream_fusion.utils.debrid.base_debrid import BaseDebrid
from stream_fusion.utils.general import season_episode_in_filename
from stream_fusion.logging_config import logger
from stream_fusion.settings import settings


class AllDebrid(BaseDebrid):
    def __init__(self, config):
        super().__init__(config)
        self.base_url = f"{settings.ad_base_url}/{settings.ad_api_version}/"
        self.agent = settings.ad_user_app

    def get_headers(self):
        if settings.ad_unique_account:
            if not settings.proxied_link:
                logger.warning("AllDebrid: Unique account enabled, but proxied link is disabled. This may lead to account ban.")
                logger.warning("AllDebrid: Please enable proxied link in the settings.")
                raise HTTPException(status_code=500, detail="Proxied link is disabled.")
            if settings.ad_token:
                return {"Authorization": f"Bearer {settings.ad_token}"}
            else:
                logger.warning("AllDebrid: Unique account enabled, but no token provided. Please provide a token in the env.")
                raise HTTPException(status_code=500, detail="AllDebrid token is not provided.")
        else:
            return {"Authorization": f"Bearer {self.config.get('ADToken')}"}

    def add_magnet(self, magnet, ip=None):
        url = f"{self.base_url}magnet/upload?agent={self.agent}"
        data = {"magnets[]": magnet}
        return self.json_response(url, method='post', headers=self.get_headers(), data=data)

    def add_torrent(self, torrent_file, ip=None):
        url = f"{self.base_url}magnet/upload/file?agent={self.agent}"
        files = {"files[]": (str(uuid.uuid4()) + ".torrent", torrent_file, 'application/x-bittorrent')}
        return self.json_response(url, method='post', headers=self.get_headers(), files=files)

    def get_magnet_files(self, id, ip=None):
        """Get files from magnet using v4 API"""
        url = f"{settings.ad_base_url}/v4/magnet/files"
        data = {"id[]": id, "agent": self.agent}
        return self.json_response(url, method='post', headers=self.get_headers(), data=data)

    def unrestrict_link(self, link, ip=None):
        url = f"{self.base_url}link/unlock?agent={self.agent}&link={link}"
        return self.json_response(url, method='get', headers=self.get_headers())

    def get_stream_link(self, query, config, ip=None):
        magnet = query['magnet']
        stream_type = query['type']
        torrent_download = unquote(query["torrent_download"]) if query["torrent_download"] is not None else None

        torrent_id = self.add_magnet_or_torrent(magnet, torrent_download, ip)
        torrent_id = str(torrent_id) if torrent_id else ""
        logger.info(f"AllDebrid: Torrent ID: {torrent_id}")

        if not torrent_id or torrent_id.startswith("Error"):
            logger.error(f"AllDebrid: Failed to add torrent: {torrent_id}")
            return settings.no_cache_video_url

        logger.info(f"AllDebrid: Retrieving data for torrent ID: {torrent_id}")
        files_response = self.get_magnet_files(torrent_id, ip)
        logger.info(f"AllDebrid: Data retrieved for torrent ID")

        if not files_response or files_response.get("status") != "success":
            logger.error("AllDebrid: Failed to get files")
            return settings.no_cache_video_url

        magnets = files_response["data"].get("magnets", [])
        if not magnets:
            logger.error("AllDebrid: No magnets in response")
            return settings.no_cache_video_url

        magnet_data = magnets[0]
        files = magnet_data.get("files", [])

        link = settings.no_cache_video_url
        if stream_type == "movie":
            logger.info("AllDebrid: Getting link for movie")
            try:
                if isinstance(files, list):
                    link = max(files, key=lambda x: x.get('s', 0))['l']
                else:
                    link = max(files.values(), key=lambda x: x.get('s', 0))['l']
            except Exception as e:
                logger.error(f"AllDebrid: Error getting movie link: {str(e)}")
        elif stream_type == "series":
            numeric_season = int(query['season'].replace("S", ""))
            numeric_episode = int(query['episode'].replace("E", ""))
            logger.info(f"AllDebrid: Getting link for series S{numeric_season:02d}E{numeric_episode:02d}")

            matching_files = []
            try:
                if isinstance(files, list):
                    files_iter = files
                else:
                    files_iter = files.values()

                for file_item in files_iter:
                    filename = file_item.get("n", "") if isinstance(file_item, dict) else ""
                    logger.debug(f"AllDebrid: Checking file: {filename}")

                    if season_episode_in_filename(filename, numeric_season, numeric_episode):
                        logger.debug(f"AllDebrid: ✓ Match found with RTN parser: {filename}")
                        matching_files.append(file_item)
                    else:
                        import re
                        episode_patterns = [
                            rf"[Ss]{numeric_season:02d}[Ee]{numeric_episode:02d}",
                            rf"[Ss]{numeric_season}[Ee]{numeric_episode:02d}",
                            rf"{numeric_season:02d}x{numeric_episode:02d}",
                            rf"{numeric_season}x{numeric_episode:02d}",
                            rf"[Ss]eason.{numeric_season:02d}.*[Ee]{numeric_episode:02d}",
                            rf"[Ss]eason.{numeric_season}.*[Ee]{numeric_episode:02d}",
                            rf"[Ss]{numeric_season:02d}.*[Ee]pisode.{numeric_episode:02d}",
                            rf"[Ss]{numeric_season}.*[Ee]pisode.{numeric_episode:02d}",
                        ]

                        match_found = False
                        for pattern in episode_patterns:
                            if re.search(pattern, filename, re.IGNORECASE):
                                logger.debug(f"AllDebrid: ✓ Match found with improved pattern '{pattern}': {filename}")
                                matching_files.append(file_item)
                                match_found = True
                                break

                        if not match_found:
                            logger.debug(f"AllDebrid: ✗ No match: {filename}")

                if len(matching_files) == 0:
                    logger.warning(f"AllDebrid: No matching files found for S{numeric_season:02d}E{numeric_episode:02d}")
                    return settings.no_cache_video_url
                else:
                    link = max(matching_files, key=lambda x: x.get("s", 0))["l"]
            except Exception as e:
                logger.error(f"AllDebrid: Error processing series: {str(e)}")
        else:
            logger.error("AllDebrid: Unsupported stream type.")
            raise HTTPException(status_code=500, detail="Unsupported stream type.")

        if link == settings.no_cache_video_url:
            logger.info("AllDebrid: Video not cached, returning NO_CACHE_VIDEO_URL")
            return link

        logger.info(f"AllDebrid: Retrieved link: {link}")

        try:
            unlocked_link_data = self.unrestrict_link(link, ip)
            if unlocked_link_data and unlocked_link_data.get("status") == "success":
                logger.info(f"AllDebrid: Unrestricted link")
                return unlocked_link_data["data"]["link"]
        except Exception as e:
            logger.debug(f"AllDebrid: Could not unrestrict link: {str(e)}")

        return link

    def get_availability_bulk(self, hashes_or_magnets, ip=None):
        if len(hashes_or_magnets) == 0:
            logger.info("AllDebrid: No hashes to be sent.")
            return {"status": "success", "data": {"magnets": []}}

        result_magnets = []
        for hash_or_magnet in hashes_or_magnets:
            try:
                result_magnets.append({
                    "hash": hash_or_magnet,
                    "instant": True,
                    "files": []
                })
            except Exception as e:
                logger.error(f"AllDebrid: Error processing hash {hash_or_magnet}: {str(e)}")
                result_magnets.append({
                    "hash": hash_or_magnet,
                    "instant": True,
                    "files": []
                })

        return {"status": "success", "data": {"magnets": result_magnets}}

    def add_magnet_or_torrent(self, magnet, torrent_download=None, ip=None):
        torrent_id = ""
        if torrent_download is None:
            logger.info(f"AllDebrid: Adding magnet")
            magnet_response = self.add_magnet(magnet, ip)
            logger.info(f"AllDebrid: Add magnet response received")

            if not magnet_response or "status" not in magnet_response or magnet_response["status"] != "success":
                return "Error: Failed to add magnet."

            torrent_id = magnet_response["data"]["magnets"][0]["id"]
        else:
            logger.info(f"AllDebrid: Downloading torrent file")
            torrent_file = self.download_torrent_file(torrent_download)
            logger.info(f"AllDebrid: Torrent file downloaded")

            logger.info(f"AllDebrid: Adding torrent file")
            upload_response = self.add_torrent(torrent_file, ip)
            logger.info(f"AllDebrid: Add torrent file response received")

            if not upload_response or "status" not in upload_response or upload_response["status"] != "success":
                return "Error: Failed to add torrent file in AllDebrid."

            torrent_id = upload_response["data"]["files"][0]["id"]

        logger.info(f"AllDebrid: New torrent ID: {torrent_id}")
        return torrent_id
