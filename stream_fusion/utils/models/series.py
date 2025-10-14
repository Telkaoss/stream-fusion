from stream_fusion.utils.models.media import Media


class Series(Media):
    def __init__(self, id, tmdb_id, titles, season, episode, languages):
        super().__init__(id, tmdb_id, titles, languages, "series")
        self.season = season
        self.episode = episode
        self.seasonfile = None

    def get_season_number(self) -> int:
        """Extract season number from season string (e.g., 'S02' -> 2)"""
        if self.season and self.season.startswith('S'):
            return int(self.season[1:])
        return 0

    def get_episode_number(self) -> int:
        """Extract episode number from episode string (e.g., 'E05' -> 5)"""
        if self.episode and self.episode.startswith('E'):
            return int(self.episode[1:])
        return 0
