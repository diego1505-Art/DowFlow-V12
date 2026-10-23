import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import playlist


class FakeYoutubeDL:
    instances = []

    def __init__(self, options):
        self.options = options
        self.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def extract_info(self, url, download=True):
        if not download:
            return {
                "title": "clip",
                "formats": [
                    {"height": 2160, "vcodec": "vp9", "filesize": 1000 * 1024 * 1024},
                    {"height": 1080, "vcodec": "h264", "filesize": 100 * 1024 * 1024},
                    {"height": 480, "vcodec": "h264", "filesize": 10 * 1024 * 1024},
                    {"height": 240, "vcodec": "h264", "filesize": 2 * 1024 * 1024},
                    {"vcodec": "none", "acodec": "aac", "filesize": 5 * 1024 * 1024},
                ],
            }
        output_path = self.options["outtmpl"].replace("%(title).200s.%(ext)s", "clip.mp4")
        Path(output_path).write_bytes(b"video")
        return {"title": "clip", "ext": "mp4", "filepath": output_path}

    def download(self, urls):
        raise RuntimeError("HTTP Error 429: Too Many Requests")


class BotCheckYoutubeDL(FakeYoutubeDL):
    """YouTube exige une session connectée tant qu'aucun cookie n'est fourni."""

    def extract_info(self, url, download=True):
        if not (self.options.get("cookiefile") or self.options.get("cookiesfrombrowser")):
            raise RuntimeError("Sign in to confirm you're not a bot. Use --cookies")
        return super().extract_info(url, download)


class BotCheckLockedChromeYoutubeDL(FakeYoutubeDL):
    """YouTube exige une session, puis Windows bloque la base de cookies de Chrome."""

    def extract_info(self, url, download=True):
        if self.options.get("cookiesfrombrowser"):
            raise RuntimeError("Could not copy Chrome cookie database: Permission denied")
        if not self.options.get("cookiefile"):
            raise RuntimeError("Sign in to confirm you're not a bot. Use --cookies")
        return super().extract_info(url, download)


class DownloadStabilityTests(unittest.TestCase):
    def setUp(self):
        FakeYoutubeDL.instances.clear()

    def test_youtube_uses_aria2_and_skips_subtitles_by_default(self):
        with patch.object(playlist.yt_dlp, "YoutubeDL", FakeYoutubeDL):
            with patch.object(
                playlist.shutil,
                "which",
                side_effect=lambda name: "aria2c.exe" if name == "aria2c" else None,
            ):
                with tempfile.TemporaryDirectory() as output:
                    result = playlist._ydlp_download(
                        "https://www.youtube.com/shorts/12345678901",
                        output_path=output,
                        quality="720p",
                    )
                    self.assertTrue(Path(result["filepath"]).is_file())

        # Une qualité fixe reste compatible avec aria2c.
        self.assertEqual(len(FakeYoutubeDL.instances), 1)
        options = FakeYoutubeDL.instances[-1].options
        self.assertNotIn("writesubtitles", options)
        self.assertEqual(options["external_downloader"], "aria2c")
        self.assertIn("--split=16", options["external_downloader_args"])
        self.assertTrue(options["quiet"])
        self.assertIn("[protocol^=http]", options["format"])
        self.assertEqual(options["retries"], 10)
        self.assertEqual(options["fragment_retries"], 10)
        self.assertEqual(options["remote_components"], ["ejs:github"])

    def test_adaptive_download_uses_native_progress_and_retries_lower_quality(self):
        class AdaptiveYoutubeDL(FakeYoutubeDL):
            calls = 0

            def extract_info(self, url, download=True):
                type(self).calls += 1
                progress_hooks = self.options.get("progress_hooks", [])
                height = 2160 if type(self).calls == 1 else 1440
                first = {
                    "status": "downloading",
                    "filename": "clip.mp4",
                    "info_dict": {"width": 2560, "height": height},
                    "downloaded_bytes": 0,
                    "total_bytes": 1700 * 1024 * 1024,
                }
                second = {
                    **first,
                    "downloaded_bytes": (11 if type(self).calls == 1 else 700) * 1024 * 1024,
                }
                with patch.object(
                    playlist.time,
                    "time",
                    side_effect=[1000.0, 1011.0],
                ):
                    for hook in progress_hooks:
                        hook(first)
                    for hook in progress_hooks:
                        hook(second)
                output_path = self.options["outtmpl"].replace("%(title).200s.%(ext)s", "clip.mp4")
                Path(output_path).write_bytes(b"video")
                return {"title": "clip", "ext": "mp4", "filepath": output_path}

        AdaptiveYoutubeDL.calls = 0
        with patch.object(playlist.yt_dlp, "YoutubeDL", AdaptiveYoutubeDL):
            with patch.object(
                playlist.shutil,
                "which",
                side_effect=lambda name: "aria2c.exe" if name == "aria2c" else None,
            ):
                with tempfile.TemporaryDirectory() as output:
                    result = playlist._ydlp_download(
                        "https://www.youtube.com/watch?v=123",
                        output_path=output,
                        quality="best",
                    )

        self.assertTrue(result["quality_changed"])
        self.assertEqual(result["quality_used"], "1440p")
        self.assertEqual(len(AdaptiveYoutubeDL.instances), 2)
        self.assertNotIn("external_downloader", AdaptiveYoutubeDL.instances[0].options)
        self.assertEqual(
            AdaptiveYoutubeDL.instances[1].options["format_sort"], ["res:1440"]
        )

    def test_auto_subtitles_only_request_french(self):
        options = playlist._build_subtitle_lang_options(None)

        self.assertEqual(options["subtitleslangs"], ["fr"])

    def test_runtime_uses_browser_cookies_for_youtube(self):
        options = playlist.yt_dlp_runtime_options()

        self.assertNotIn("cookiesfrombrowser", options)
        youtube_options = playlist.yt_dlp_runtime_options(include_browser_cookies=True)
        self.assertEqual(youtube_options["cookiesfrombrowser"], ("chrome",))

    def test_youtube_first_attempt_uses_no_cookies(self):
        with patch.object(playlist.yt_dlp, "YoutubeDL", FakeYoutubeDL):
            with tempfile.TemporaryDirectory() as output:
                result = playlist._ydlp_download(
                    "https://www.youtube.com/shorts/12345678901",
                    output_path=output,
                )
                self.assertTrue(Path(result["filepath"]).is_file())

        self.assertEqual(len(FakeYoutubeDL.instances), 1)
        options = FakeYoutubeDL.instances[-1].options
        self.assertNotIn("cookiesfrombrowser", options)
        self.assertNotIn("cookiefile", options)

    def test_youtube_bot_check_retries_with_exported_cookie_file(self):
        with tempfile.TemporaryDirectory() as output:
            cookie_file = Path(output) / "youtube_cookies.txt"
            cookie_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
            with patch.object(playlist, "YOUTUBE_COOKIES_FILE", cookie_file):
                with patch.object(playlist.yt_dlp, "YoutubeDL", BotCheckYoutubeDL):
                    result = playlist._ydlp_download(
                        "https://www.youtube.com/shorts/12345678901",
                        output_path=output,
                        quality="240p",
                    )
                    self.assertTrue(Path(result["filepath"]).is_file())

        # Pas d'analyse adaptative car quality="240p" (pas "best")
        # Donc 1er essai sans cookies, puis 2e essai avec cookies.
        self.assertEqual(len(BotCheckYoutubeDL.instances), 2)
        first_options = BotCheckYoutubeDL.instances[0].options
        self.assertNotIn("cookiesfrombrowser", first_options)
        self.assertNotIn("cookiefile", first_options)
        retry_options = BotCheckYoutubeDL.instances[1].options
        self.assertEqual(retry_options["cookiefile"], str(cookie_file))
        self.assertNotIn("cookiesfrombrowser", retry_options)

    def test_youtube_bot_check_without_cookie_file_reports_actionable_error(self):
        with patch.object(playlist.yt_dlp, "YoutubeDL", BotCheckLockedChromeYoutubeDL):
            with tempfile.TemporaryDirectory() as output:
                with patch.object(playlist, "YOUTUBE_COOKIES_FILE", Path(output) / "missing.txt"):
                    with self.assertRaisesRegex(RuntimeError, "youtube_cookies.txt"):
                        playlist._ydlp_download(
                            "https://www.youtube.com/shorts/12345678901",
                            output_path=output,
                            quality="240p",
                        )

        # 4 instances : Turbo (sans cookies, puis Chrome cookies) -> Fallback Native (sans cookies, puis Chrome cookies)
        self.assertEqual(len(BotCheckLockedChromeYoutubeDL.instances), 4)
        self.assertNotIn(
            "cookiesfrombrowser", BotCheckLockedChromeYoutubeDL.instances[0].options
        )
        self.assertEqual(
            BotCheckLockedChromeYoutubeDL.instances[1].options["cookiesfrombrowser"],
            ("chrome",),
        )

    def test_instagram_access_denied_retries_with_cookies(self):
        class InstagramAccessDeniedDL(FakeYoutubeDL):
            def extract_info(self, url, download=True):
                if not (self.options.get("cookiefile") or self.options.get("cookiesfrombrowser")):
                    raise RuntimeError("Streams trouvés mais inaccessibles : ['blob:https://instagram.com/123']")
                return super().extract_info(url, download)

        with patch.object(playlist.yt_dlp, "YoutubeDL", InstagramAccessDeniedDL):
            with tempfile.TemporaryDirectory() as output:
                # On simule la présence de cookies Chrome
                with patch.object(playlist, "yt_dlp_runtime_options", side_effect=lambda include_browser_cookies=False: 
                                  {"cookiesfrombrowser": ("chrome",)} if include_browser_cookies else {}):
                    result = playlist._ydlp_download(
                        "https://www.instagram.com/reel/123/",
                        output_path=output,
                        quality="240p",
                    )
                    self.assertTrue(Path(result["filepath"]).is_file())

        self.assertEqual(len(InstagramAccessDeniedDL.instances), 2)
        self.assertIn("cookiesfrombrowser", InstagramAccessDeniedDL.instances[1].options)

    def test_twitter_output_folder_generation(self):
        folder = playlist._build_output_folder("https://twitter.com/user/status/123", "downloads")
        self.assertEqual(folder, os.path.join("downloads", "twitter"))

    def test_chrome_cookie_lock_has_actionable_message(self):
        message = playlist._friendly_cookie_error(
            RuntimeError("Could not copy Chrome cookie database: Permission denied")
        )

        self.assertIsNotNone(message)
        self.assertIn("youtube_cookies.txt", message)
        self.assertIn("ne le partagez pas", message)
        self.assertIn("YouTube/Instagram", message)

    def test_other_sites_use_aria2_when_available(self):
        with patch.object(playlist.yt_dlp, "YoutubeDL", FakeYoutubeDL):
            with patch.object(
                playlist.shutil,
                "which",
                side_effect=lambda name: "aria2c.exe" if name == "aria2c" else None,
            ):
                with tempfile.TemporaryDirectory() as output:
                    result = playlist._ydlp_download(
                        "https://www.instagram.com/reel/example/",
                        output_path=output,
                        quality="720p",
                    )
                    self.assertTrue(Path(result["filepath"]).is_file())

        self.assertEqual(FakeYoutubeDL.instances[-1].options["external_downloader"], "aria2c")

    def test_subtitles_are_requested_in_main_download_when_enabled(self):
        with patch.object(playlist.yt_dlp, "YoutubeDL", FakeYoutubeDL):
            with tempfile.TemporaryDirectory() as output:
                result = playlist._ydlp_download(
                    "https://example.com/video",
                    output_path=output,
                    write_subtitles=True,
                    language="fr",
                )
                self.assertTrue(Path(result["filepath"]).is_file())

        # Passe unique : les sous-titres sont récupérés dans le même appel que la
        # vidéo, plus de second `yt-dlp` dédié (source des erreurs 429).
        self.assertEqual(len(FakeYoutubeDL.instances), 1)
        options = FakeYoutubeDL.instances[0].options
        self.assertTrue(options["writesubtitles"])
        self.assertTrue(options["writeautomaticsub"])
        self.assertEqual(options["subtitlesformat"], "vtt/best")
        self.assertIn("fr", options["subtitleslangs"])

    def test_subtitle_http_429_does_not_fail_video_download(self):
        class Subtitle429YoutubeDL(FakeYoutubeDL):
            def _write_subtitles(self, info_dict, filename):
                raise playlist.yt_dlp.utils.DownloadError("HTTP Error 429: Too Many Requests")

            def extract_info(self, url, download=True):
                self._write_subtitles({}, "clip.mp4")
                return super().extract_info(url, download)

        with patch.object(playlist.yt_dlp, "YoutubeDL", Subtitle429YoutubeDL):
            with tempfile.TemporaryDirectory() as output:
                result = playlist._ydlp_download(
                    "https://www.youtube.com/watch?v=123",
                    output_path=output,
                    write_subtitles=True,
                    language="fr",
                )
                self.assertTrue(Path(result["filepath"]).is_file(), repr(result))
        self.assertEqual(len(Subtitle429YoutubeDL.instances), 1)

    def test_subtitles_are_converted_to_vtt_when_ffmpeg_available(self):
        with patch.object(playlist, "_resolve_ffmpeg_dir", return_value="/fake/ffmpeg"):
            with patch.object(playlist.yt_dlp, "YoutubeDL", FakeYoutubeDL):
                with tempfile.TemporaryDirectory() as output:
                    playlist._ydlp_download(
                        "https://example.com/video",
                        output_path=output,
                        write_subtitles=True,
                        language="fr",
                    )

        postprocessors = FakeYoutubeDL.instances[0].options["postprocessors"]
        self.assertTrue(
            any(
                pp.get("key") == "FFmpegSubtitlesConvertor" and pp.get("format") == "vtt"
                for pp in postprocessors
            )
        )

    def test_adaptive_watch_hook_downgrades_when_eta_is_too_long(self):
        hook = playlist._make_adaptive_watch_hook()
        first = {
            "status": "downloading",
            "filename": "video.f137.mp4",
            "info_dict": {"width": 3840, "height": 2160},
            "downloaded_bytes": 0,
            "total_bytes": 1000 * 1024 * 1024,
        }
        # 11 Mo en 11 s → ~1 Mo/s : ETA ≈ 989 s > seuil 4K (300 s)
        second = {**first, "downloaded_bytes": 11 * 1024 * 1024}

        base = 1000.0
        with patch.object(playlist.time, "time", side_effect=[base, base + 11.0]):
            hook(first)  # démarre la fenêtre d'observation
            with self.assertRaises(playlist._AdaptiveDowngrade) as ctx:
                hook(second)  # dépasse la fenêtre de 10 s : décision de downgrade

        self.assertEqual(ctx.exception.quality, "1440p")
        self.assertIn("1440p", ctx.exception.note)

    def test_adaptive_watch_hook_keeps_quality_when_eta_is_short(self):
        hook = playlist._make_adaptive_watch_hook()
        first = {
            "status": "downloading",
            "filename": "video.f137.mp4",
            "info_dict": {"width": 3840, "height": 2160},
            "downloaded_bytes": 0,
            "total_bytes": 1000 * 1024 * 1024,
        }
        # 700 Mo en 11 s → ~64 Mo/s : ETA quelques secondes, largement sous le seuil
        second = {**first, "downloaded_bytes": 700 * 1024 * 1024}

        base = 2000.0
        with patch.object(playlist.time, "time", side_effect=[base, base + 11.0]):
            hook(first)
            hook(second)  # doit se terminer sans lever d'exception

    def test_adaptive_watch_hook_resets_for_new_file_and_can_retry(self):
        hook = playlist._make_adaptive_watch_hook()
        first = {
            "status": "downloading",
            "filename": "video-1.f137.mp4",
            "info_dict": {"width": 3840, "height": 2160},
            "downloaded_bytes": 0,
            "total_bytes": 1000 * 1024 * 1024,
        }
        short = {**first, "downloaded_bytes": 700 * 1024 * 1024}
        second = {
            "status": "downloading",
            "filename": "video-2.f137.mp4",
            "info_dict": {"width": 3840, "height": 2160},
            "downloaded_bytes": 0,
            "total_bytes": 1000 * 1024 * 1024,
        }
        slow = {**second, "downloaded_bytes": 11 * 1024 * 1024}

        base = 2500.0
        with patch.object(playlist.time, "time", side_effect=[base, base + 11.0, base + 12.0, base + 23.0, base + 24.0]):
            hook(first)
            hook(short)
            hook(second)
            with self.assertRaises(playlist._AdaptiveDowngrade):
                hook(slow)

    def test_quality_target_uses_resolution_sort_for_portrait_videos(self):
        selector = playlist._build_video_format_string("240p", "mp4", True, prefer_http=True)

        # Un filtre `height<=240` ne matcherait rien sur un Short vertical (144x256)
        self.assertNotIn("height<=", selector)
        self.assertIn("[protocol^=http]", selector)
        self.assertTrue(selector.endswith("bestvideo*+bestaudio/best"))
        self.assertEqual(playlist._build_video_format_sort("240p"), ["res:240"])
        self.assertIsNone(playlist._build_video_format_sort("best"))

    def test_quality_target_is_forwarded_to_yt_dlp_options(self):
        with patch.object(playlist.yt_dlp, "YoutubeDL", FakeYoutubeDL):
            with tempfile.TemporaryDirectory() as output:
                playlist._ydlp_download(
                    "https://www.youtube.com/shorts/12345678901",
                    output_path=output,
                    quality="720p",
                )

        self.assertEqual(FakeYoutubeDL.instances[0].options["format_sort"], ["res:720"])

    def test_adaptive_quality_attaches_watch_hook_only_when_enabled(self):
        with patch.object(playlist.yt_dlp, "YoutubeDL", FakeYoutubeDL):
            with tempfile.TemporaryDirectory() as output:
                # Activé (par défaut) → un hook de surveillance est branché
                playlist._ydlp_download(
                    "https://www.youtube.com/watch?v=123",
                    output_path=output,
                    quality="best",
                    adaptive_quality=True,
                )
                # Désactivé → aucune surveillance temps réel
                playlist._ydlp_download(
                    "https://www.youtube.com/watch?v=123",
                    output_path=output,
                    quality="best",
                    adaptive_quality=False,
                )

        self.assertIn("progress_hooks", FakeYoutubeDL.instances[-2].options)
        self.assertNotIn("progress_hooks", FakeYoutubeDL.instances[-1].options)


if __name__ == "__main__":
    unittest.main()
