"""验证网页只保留原项目C汉化界面，测试不连接ADC或执行机械命令。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from interface import create_app
from update_on_wujing import app_file


class ProjectCWebTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app({"TESTING": True})
        self.client = self.app.test_client()

    def test_original_chinese_home_without_hardware_connections(self):
        """汉化版应继续显示PCB、串口、探针和导入入口。"""
        with patch("interface.serial.serial.Serial") as connection:
            response = self.client.get("/")
            self.assertEqual(response.status_code, 200)
            page = response.data.decode("utf-8")
            for text in ('lang="zh-CN"', "CircuitScout · 悟净", "串口连接", "探针控制", "查找候选测点", "设置"):
                self.assertIn(text, page)
            for text in ("measurements.js", "components.js", "测量工作台", "同步 IQ 分析"):
                self.assertNotIn(text, page)
            connection.assert_not_called()

    def test_added_pages_api_and_assets_are_removed(self):
        """撤下页面的后端也必须撤下，不仅隐藏导航。"""
        for route in ("/measurements", "/probes", "/api/measurements/status", "/api/measurements/records",
                      "/api/components/status", "/api/components/example/C", "/static/js/measurements.js",
                      "/static/css/measurements.css", "/static/js/components.js", "/static/css/components.css"):
            self.assertEqual(self.client.get(route).status_code, 404, route)
        for route in ("/api/measurements/capture", "/api/components/capture"):
            self.assertEqual(self.client.post(route, json={}).status_code, 404, route)
        for asset in ("/static/js/main.js", "/static/js/socket.js", "/static/js/kicanvas/kicanvas.js", "/static/css/main.css"):
            response = self.client.get(asset)
            self.assertEqual(response.status_code, 200, asset)
            response.close()

    def test_update_paths_cannot_touch_persistent_data(self):
        """版本内文件更新不得越界或进入保存用户文件的上传目录。"""
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / "app"
            app.mkdir()
            self.assertEqual(app_file(app, "interface/__init__.py"), app / "interface/__init__.py")
            for name in ("../data/raw.bin", "interface/static/uploads/user.kicad_pcb", str(Path(directory) / "outside.py")):
                with self.assertRaises(RuntimeError):
                    app_file(app, name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
