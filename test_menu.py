import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import app
from menu_config import MENU_CONFIG_PATH, load_menu


class MenuExportTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.menu_path = Path(self.temp_dir.name) / "menu.json"
        self.path_patch = patch("menu_config.MENU_CONFIG_PATH", self.menu_path)
        self.path_patch.start()

    def tearDown(self):
        self.path_patch.stop()
        self.temp_dir.cleanup()

    def test_rename_persists_and_is_visible_in_menu(self):
        response = self.client.post("/api/menu/rename", json={"id": "music", "label": "Ma bibliothèque"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(load_menu()["items"][1]["label"], "Ma bibliothèque")
        self.assertIn("Ma bibliothèque".encode(), self.client.get("/").data)

    def test_export_can_target_one_submenu(self):
        response = self.client.get("/api/menu/export?id=music&name=Audio")
        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Disposition"], 'attachment; filename="Audio.json"')
        self.assertEqual(payload["items"][0]["id"], "music")
        self.assertEqual(payload["items"][0]["label"], "Musique")


if __name__ == "__main__":
    unittest.main()