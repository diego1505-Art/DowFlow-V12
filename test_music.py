import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import music
import playlist


class FakeSearchYoutubeDL:
    """Simule une recherche yt-dlp (extract_flat) sans accès réseau."""

    def __init__(self, options):
        self.options = options

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def extract_info(self, url, download=False):
        return {
            "entries": [
                {"id": "abc123", "title": "Titre A", "duration": 215, "uploader": "Artiste A"},
                None,
                {"id": "abc123", "title": "Doublon", "duration": 215},
                {"id": "def456", "title": "Titre B", "duration": 180, "uploader": "Artiste B"},
            ]
        }


class FakeResponse:
    def __init__(self, payload=None, content=b""):
        self._payload = payload if payload is not None else {}
        self.content = content

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class MusicHelperTests(unittest.TestCase):
    def test_video_id_from_url(self):
        self.assertEqual(music._video_id_from_url("https://www.youtube.com/watch?v=abc123"), "abc123")
        self.assertEqual(music._video_id_from_url("https://youtu.be/xyz789"), "xyz789")
        self.assertEqual(music._video_id_from_url("https://www.youtube.com/shorts/short42"), "short42")
        self.assertIsNone(music._video_id_from_url("https://vimeo.com/12345"))

    def test_normalize_entry_builds_thumbnail_and_url(self):
        track = music._normalize_entry({"id": "vid1", "title": "Son", "duration": 42.7})
        self.assertEqual(track["url"], "https://www.youtube.com/watch?v=vid1")
        self.assertEqual(track["duration"], 42)
        self.assertIn("vid1", track["thumbnail"])

    def test_dedupe_keeps_first_occurrence(self):
        tracks = [{"id": "1"}, {"id": "2"}, {"id": "1"}]
        self.assertEqual([t["id"] for t in music._dedupe(tracks)], ["1", "2"])

    def test_dedupe_by_title_when_same_track_has_different_ids(self):
        tracks = [
            {"id": "id-a", "title": "Imagine Dragons", "artist": "Night Visions"},
            {"id": "id-b", "title": "Imagine Dragons", "artist": "Night Visions"},
            {"id": "id-c", "title": "Other Song", "artist": "Artist"},
        ]
        deduped = music._dedupe(tracks)
        self.assertEqual(len(deduped), 2)
        self.assertEqual(deduped[0]["title"], "Imagine Dragons")
        self.assertEqual(deduped[1]["title"], "Other Song")


class MusicSearchTests(unittest.TestCase):
    def test_search_dedupes_and_limits(self):
        with patch("yt_dlp.YoutubeDL", FakeSearchYoutubeDL):
            tracks = music.search_music("test", limit=10)
        self.assertEqual([t["id"] for t in tracks], ["abc123", "def456"])
        self.assertEqual(tracks[0]["artist"], "Artiste A")

    def test_empty_query_returns_nothing(self):
        self.assertEqual(music.search_music("   "), [])


class MusicCoverTests(unittest.TestCase):
    def test_fetch_cover_uses_deezer(self):
        payload = {"data": [{"album": {"cover_xl": "https://cdn/cover.jpg"}}]}
        with patch.object(music.requests, "get", return_value=FakeResponse(payload)):
            self.assertEqual(music.fetch_cover_url("Artiste", "Titre"), "https://cdn/cover.jpg")

    def test_fetch_cover_returns_none_without_title(self):
        self.assertIsNone(music.fetch_cover_url("Artiste", ""))


class MusicDownloadTests(unittest.TestCase):
    def test_download_uses_music_folder_and_saves_cover(self):
        with tempfile.TemporaryDirectory() as output:
            media_path = Path(output) / "music" / "Titre.mp3"
            media_path.parent.mkdir(parents=True)
            media_path.write_bytes(b"audio")

            def fake_download_media(url, **kwargs):
                self.assertEqual(kwargs["force_folder"], music.MUSIC_SUBFOLDER)
                self.assertEqual(kwargs["download_type"], "audio")
                return {"filepath": str(media_path), "filename": "Titre.mp3"}

            cover_payload = {"data": [{"album": {"cover_xl": "https://cdn/cover.jpg"}}]}

            def fake_get(url, **kwargs):
                if "deezer" in url:
                    return FakeResponse(cover_payload)
                return FakeResponse(content=b"image-bytes")

            with patch.object(playlist, "download_media", side_effect=fake_download_media):
                with patch.object(music.requests, "get", side_effect=fake_get):
                    result = music.download_music_track(
                        "https://www.youtube.com/watch?v=abc123",
                        output_path=output,
                        audio_format="mp3",
                        with_cover=True,
                        title="Titre",
                        artist="Artiste",
                    )

            self.assertTrue(Path(result["cover_path"]).is_file())
            self.assertEqual(result["cover_url"], "/media/music/Titre.jpg")


class MusicLibraryTests(unittest.TestCase):
    def test_list_library_pairs_audio_with_cover(self):
        with tempfile.TemporaryDirectory() as folder:
            music_dir = Path(folder)
            (music_dir / "Morceau.mp3").write_bytes(b"audio")
            (music_dir / "Morceau.jpg").write_bytes(b"image")
            (music_dir / "ignore.txt").write_text("x", encoding="utf-8")

            with patch.object(music, "MUSIC_FOLDER", music_dir):
                items = music.list_library()

            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["title"], "Morceau")
            self.assertIn("Morceau.mp3", items[0]["audio_url"])
            self.assertIsNotNone(items[0]["cover_url"])

    def test_list_library_deduplicates_lyric_and_official_video_versions(self):
        with tempfile.TemporaryDirectory() as folder:
            music_dir = Path(folder)
            (music_dir / "Imagine-Dragons---Demons-Lyrics.mp3").write_bytes(b"audio-one")
            (music_dir / "Imagine-Dragons---Demons-Official-Music-Video.mp3").write_bytes(b"audio-two")
            (music_dir / "Imagine-Dragons---Demons-Lyrics.jpg").write_bytes(b"cover-one")
            (music_dir / "Imagine-Dragons---Demons-Official-Music-Video.jpg").write_bytes(b"cover-two")

            with patch.object(music, "MUSIC_FOLDER", music_dir):
                items = music.list_library()

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "Demons")
        self.assertEqual(items[0]["artist"], "Imagine Dragons")


if __name__ == "__main__":
    unittest.main()
