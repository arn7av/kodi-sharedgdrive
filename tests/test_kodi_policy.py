import ast
import pathlib
import unittest
import xml.etree.ElementTree as ElementTree


ROOT = pathlib.Path(__file__).resolve().parents[1]
KODI_PLUGIN = ROOT / "resources/lib/kodi_plugin.py"
SETTINGS = ROOT / "resources/settings.xml"
STRINGS = ROOT / "resources/language/resource.language.en_gb/strings.po"


def _constant_value(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        return _constant_value(node.left) * _constant_value(node.right)
    raise ValueError("unsupported policy expression")


class KodiPolicyTests(unittest.TestCase):
    def test_debug_input_stays_out_of_persistent_and_plugin_routes(self):
        module = ast.parse(KODI_PLUGIN.read_text(encoding="utf-8"))
        plugin_class = next(
            node for node in module.body
            if isinstance(node, ast.ClassDef) and node.name == "KodiPlugin"
        )
        debug_method = next(
            node for node in plugin_class.body
            if isinstance(node, ast.FunctionDef) and node.name == "_debug_play"
        )
        calls = [
            node for node in ast.walk(debug_method)
            if isinstance(node, ast.Call)
        ]
        attributes = {
            node.func.attr for node in calls
            if isinstance(node.func, ast.Attribute)
        }
        names = {
            node.func.id for node in calls
            if isinstance(node.func, ast.Name)
        }

        self.assertIn("input", attributes)
        self.assertIn("play", attributes)
        self.assertIn("get_debug_playback_url", attributes)
        self.assertIn("parse_debug_file_reference", names)
        self.assertNotIn("_plugin_url", attributes)
        self.assertNotIn("setSettingString", attributes)
        self.assertNotIn("SnapshotExporter", names)
        self.assertNotIn("StaleExportManager", names)

    def test_debug_playback_help_explains_resourcekey_remediation(self):
        settings = ElementTree.parse(SETTINGS)
        debug_setting = settings.find(".//setting[@id='debug_play_drive_file']")
        self.assertEqual("30057", debug_setting.attrib["help"])

        strings = STRINGS.read_text(encoding="utf-8")
        help_entry = strings.split('msgctxt "#30057"', 1)[1].split("\n\n", 1)[0]
        self.assertIn("resourcekey", help_entry)
        self.assertIn("service-account email", help_entry)
        self.assertIn("configured shared drive", help_entry)

    def test_playback_and_export_require_fifty_five_minutes(self):
        module = ast.parse(KODI_PLUGIN.read_text(encoding="utf-8"))
        values = {}
        for statement in module.body:
            if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
                continue
            target = statement.targets[0]
            if isinstance(target, ast.Name) and target.id.startswith("_"):
                values[target.id] = _constant_value(statement.value)

        self.assertEqual(55 * 60, values["_PLAYBACK_MINIMUM_TOKEN_SECONDS"])
        self.assertEqual(55 * 60, values["_EXPORT_MINIMUM_TOKEN_SECONDS"])


if __name__ == "__main__":
    unittest.main()
