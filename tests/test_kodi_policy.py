import ast
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
KODI_PLUGIN = ROOT / "resources/lib/kodi_plugin.py"


def _constant_value(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        return _constant_value(node.left) * _constant_value(node.right)
    raise ValueError("unsupported policy expression")


class KodiPolicyTests(unittest.TestCase):
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
