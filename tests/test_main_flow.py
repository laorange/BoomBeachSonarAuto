import importlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


class FakeAdb:
    instances = []

    def __init__(self, *args, **kwargs):
        self.calls = []
        self.weak_network_enabled = False
        self.reject_network_enabled = False
        FakeAdb.instances.append(self)

    def delay(self, seconds):
        self.calls.append(("delay", seconds))
        return self

    def close_app(self, package_name):
        self.calls.append(("close_app", package_name))

    def open_app(self, package_name):
        self.calls.append(("open_app", package_name))
        return self

    def click(self, x, y):
        self.calls.append(("click", x, y))

    def read_screenshot(self, output_path=None):
        self.calls.append(("read_screenshot", output_path))
        return object()

    def get_screenshot_size(self):
        self.calls.append(("get_screenshot_size",))
        return 1280, 720

    def swipe(self, start_x, start_y, end_x, end_y):
        self.calls.append(("swipe", start_x, start_y, end_x, end_y))
        return self

    def enable_weak_network(self, package_name):
        self.calls.append(("enable_weak_network", package_name))
        self.weak_network_enabled = True

    def disable_weak_network(self, package_name):
        self.calls.append(("disable_weak_network", package_name))
        self.weak_network_enabled = False

    def enable_reject_network(self, package_name):
        self.calls.append(("enable_reject_network", package_name))
        self.reject_network_enabled = True

    def disable_reject_network(self, package_name):
        self.calls.append(("disable_reject_network", package_name))
        self.reject_network_enabled = False

    def is_weak_network_enabled(self, package_name):
        self.calls.append(("is_weak_network_enabled", package_name))
        return self.weak_network_enabled

    def is_reject_network_enabled(self, package_name):
        self.calls.append(("is_reject_network_enabled", package_name))
        return self.reject_network_enabled

    def ensure_root_shell(self):
        self.calls.append(("ensure_root_shell",))


class DummyMatch:
    def __init__(self, center):
        self.center = center


class MainFlowTest(unittest.TestCase):
    def setUp(self):
        FakeAdb.instances.clear()
        self.utils = importlib.import_module("utils")
        self.original_adb_controller = self.utils.AdbController
        self.utils.AdbController = FakeAdb
        sys.modules.pop("main", None)
        self.main = importlib.import_module("main")
        self.adb = self.main.adb

    def tearDown(self):
        sys.modules.pop("main", None)
        self.utils.AdbController = self.original_adb_controller
        FakeAdb.instances.clear()

    def make_level_one_result(self):
        return self.main.LevelScanResult(
            level=1,
            hit_map=[
                [0, 0, 0],
                [1, 1, 1],
                [0, 0, 0],
            ],
            click_points=[
                (10, 10),
                (20, 20),
                (30, 30),
                (625, 300),
                (664, 328),
                (705, 358),
                (70, 70),
                (80, 80),
                (90, 90),
            ],
            base_img=object(),
            grid_quad=object(),
            output_path=Path("outputs/hit_map_level_1.png"),
        )

    def test_manual_checkpoint_continues_only_for_empty_input(self):
        with patch("builtins.input", return_value=""):
            self.main.manual_checkpoint("继续")

        with patch("builtins.input", return_value="stop"):
            with self.assertRaises(self.main.ManualStepAborted):
                self.main.manual_checkpoint("中止")

    def test_level_scan_result_maps_hit_cells_to_saved_points(self):
        result = self.make_level_one_result()

        self.assertEqual(result.hit_cells, [(1, 0), (1, 1), (1, 2)])
        self.assertEqual(
            result.hit_points,
            [
                ((1, 0), (625, 300)),
                ((1, 1), (664, 328)),
                ((1, 2), (705, 358)),
            ],
        )

    def test_start_position_is_detected_from_current_screenshot(self):
        def detail_match(_screenshot, template_path):
            if template_path == self.main.QUIT_ACTIVITY_TEMPLATE:
                return DummyMatch((40, 38))
            return None

        with patch.object(self.main, "find_template", side_effect=detail_match):
            self.assertTrue(self.main.detect_start_in_activity())

        def home_match(_screenshot, template_path):
            if template_path == self.main.ACTIVITY_BUTTON_TEMPLATE:
                return DummyMatch((1249, 269))
            return None

        with patch.object(self.main, "find_template", side_effect=home_match):
            self.assertFalse(self.main.detect_start_in_activity())

    def test_detail_start_is_normalized_through_full_activity_entry(self):
        expected = self.make_level_one_result()

        with (
            patch.object(
                self.main,
                "wait_until_occur",
                return_value=DummyMatch((40, 38)),
            ),
            patch.object(self.main, "click_template", return_value=True) as quit_click,
            patch.object(self.main, "enter_activity") as enter,
            patch.object(
                self.main,
                "handle_game_level",
                return_value=(object(), object(), expected.click_points),
            ),
            patch.object(self.main, "_save_level_scan", return_value=expected),
        ):
            result = self.main.discover_level(1, already_in_activity=True)

        self.assertIs(result, expected)
        quit_click.assert_called_once()
        enter.assert_called_once_with()
        self.assertIn(
            ("enable_weak_network", self.main.GAME_PACKAGE_NAME),
            self.adb.calls,
        )

    def test_prepare_online_replay_closes_app_before_restoring_network(self):
        result = self.make_level_one_result()
        self.adb.weak_network_enabled = True
        waits = iter(
            [
                DummyMatch((10, 20)),  # 登录按钮
                DummyMatch((30, 40)),  # 活动入口
                DummyMatch((50, 60)),  # 活动详情页
            ]
        )

        with patch.object(
            self.main,
            "wait_until_occur",
            side_effect=lambda *args, **kwargs: next(waits),
        ):
            self.main.prepare_online_replay(result, manual_steps=False)

        package_name = self.main.GAME_PACKAGE_NAME
        close_index = self.adb.calls.index(("close_app", package_name))
        disable_index = self.adb.calls.index(("disable_weak_network", package_name))
        open_index = self.adb.calls.index(("open_app", package_name))
        self.assertLess(close_index, disable_index)
        self.assertLess(disable_index, open_index)
        self.assertIn(("click", 10, 20), self.adb.calls)
        self.assertIn(("click", 30, 40), self.adb.calls)
        self.assertIn(("click", 1205, 644), self.adb.calls)

    def test_validation_rejects_incomplete_hit_map_before_any_replay(self):
        result = self.make_level_one_result()
        result.hit_map[1][2] = 0
        self.adb.weak_network_enabled = True

        with self.assertRaisesRegex(RuntimeError, "命中格数量异常"):
            self.main.validate_replay_ready(result)

        self.assertNotIn("click", [call[0] for call in self.adb.calls])

    def test_replay_clicks_first_two_then_remaining_hit_points(self):
        result = self.make_level_one_result()

        with (
            patch.object(
                self.main,
                "_save_replay_screenshot",
                side_effect=lambda level, name: Path(f"{level}_{name}.png"),
            ),
            patch.object(self.main, "find_template", return_value=None),
        ):
            self.main.replay_discovered_hits(result, manual_steps=False)

        clicks = [call for call in self.adb.calls if call[0] == "click"]
        self.assertEqual(
            clicks,
            [
                ("click", 625, 300),
                ("click", 664, 328),
                ("click", 705, 358),
            ],
        )

    def test_safe_advance_uses_one_then_three_taps_at_calibrated_point(self):
        with patch.object(
            self.main,
            "_save_replay_screenshot",
            side_effect=lambda level, name: Path(f"{level}_{name}.png"),
        ):
            self.main.advance_with_safe_taps(1, manual_steps=False)

        safe_click = ("click", *self.main.SAFE_ADVANCE_POINT)
        self.assertEqual(self.adb.calls.count(safe_click), 4)

    def test_two_level_run_reuses_the_advanced_activity_page(self):
        first = self.make_level_one_result()
        second = self.make_level_one_result()
        second.level = 2
        second.output_path = Path("outputs/hit_map_level_2.png")

        with (
            patch.object(
                self.main,
                "discover_level",
                side_effect=[first, second],
            ) as discover,
            patch.object(self.main, "prepare_online_replay"),
            patch.object(self.main, "replay_discovered_hits"),
            patch.object(self.main, "advance_with_safe_taps"),
        ):
            outputs = self.main.run_confirmed_levels(
                start_level=1,
                level_count=2,
                manual_steps=False,
            )

        self.assertEqual(
            discover.call_args_list,
            [
                unittest.mock.call(1, already_in_activity=None),
                unittest.mock.call(2, already_in_activity=True),
            ],
        )
        self.assertEqual(
            outputs,
            [
                Path("outputs/hit_map_level_1.png"),
                Path("outputs/hit_map_level_2.png"),
            ],
        )

    def test_manual_recovery_offer_cancel_keeps_network_rules_untouched(self):
        self.adb.weak_network_enabled = True
        self.adb.reject_network_enabled = True

        result = self.main.offer_manual_network_recovery(lambda: False)

        self.assertFalse(result)
        self.assertTrue(self.adb.weak_network_enabled)
        self.assertTrue(self.adb.reject_network_enabled)
        self.assertEqual(self.adb.calls, [])

    def test_manual_recovery_offer_clears_rules_without_clicking_game(self):
        package_name = self.main.GAME_PACKAGE_NAME
        self.adb.weak_network_enabled = True
        self.adb.reject_network_enabled = True

        result = self.main.offer_manual_network_recovery(lambda: True)

        self.assertTrue(result)
        self.assertFalse(self.adb.weak_network_enabled)
        self.assertFalse(self.adb.reject_network_enabled)
        self.assertNotIn("click", [call[0] for call in self.adb.calls])
        disable_reject = self.adb.calls.index(("disable_reject_network", package_name))
        disable_drop = self.adb.calls.index(("disable_weak_network", package_name))
        self.assertLess(disable_reject, disable_drop)

    def test_manual_recovery_offer_reports_failure_when_drop_remains(self):
        self.adb.weak_network_enabled = True

        def leave_drop_enabled(package_name):
            self.adb.calls.append(("disable_weak_network", package_name))

        self.adb.disable_weak_network = leave_drop_enabled
        result = self.main.offer_manual_network_recovery(lambda: True)

        self.assertFalse(result)
        self.assertTrue(self.adb.weak_network_enabled)

    def test_enter_activity_recovers_after_activity_button_missing(self):
        waits = iter(
            [
                None,
                DummyMatch((10, 20)),
                DummyMatch((30, 40)),
                DummyMatch((50, 60)),
            ]
        )

        with patch.object(
            self.main,
            "wait_until_occur",
            side_effect=lambda *args, **kwargs: next(waits),
        ):
            self.main.enter_activity(max_retries=2)

        package_name = self.main.GAME_PACKAGE_NAME
        self.assertEqual(self.adb.calls.count(("close_app", package_name)), 1)
        self.assertEqual(self.adb.calls.count(("open_app", package_name)), 1)
        self.assertIn(("click", 10, 20), self.adb.calls)
        self.assertIn(("click", 30, 40), self.adb.calls)
        self.assertIn(("click", 1205, 644), self.adb.calls)
        self.assertEqual(self.adb.calls.count(("enable_weak_network", package_name)), 1)
        self.assertEqual(
            [
                call
                for call in self.adb.calls
                if call == ("swipe", 1000, 660, 1000, 180)
            ],
            [
                ("swipe", 1000, 660, 1000, 180),
                ("swipe", 1000, 660, 1000, 180),
            ],
        )

    def test_enter_activity_stops_after_max_retries(self):
        with patch.object(self.main, "wait_until_occur", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "最大重试次数 2"):
                self.main.enter_activity(max_retries=2)

        package_name = self.main.GAME_PACKAGE_NAME
        self.assertEqual(self.adb.calls.count(("close_app", package_name)), 2)
        self.assertEqual(self.adb.calls.count(("open_app", package_name)), 2)

    def test_re_enter_skips_first_enter_only_actions(self):
        waits = iter(
            [
                DummyMatch((30, 40)),
                DummyMatch((50, 60)),
            ]
        )

        with patch.object(
            self.main,
            "wait_until_occur",
            side_effect=lambda *args, **kwargs: next(waits),
        ):
            self.main.enter_activity(re_enter=True, max_retries=1)

        package_name = self.main.GAME_PACKAGE_NAME
        self.assertNotIn(("enable_weak_network", package_name), self.adb.calls)
        self.assertNotIn(("swipe", 1000, 660, 1000, 180), self.adb.calls)
        self.assertIn(("click", 30, 40), self.adb.calls)
        self.assertIn(("click", 1205, 644), self.adb.calls)

    def test_re_enter_failure_does_not_use_normal_restart_recovery(self):
        with patch.object(self.main, "wait_until_occur", return_value=None):
            with self.assertRaisesRegex(
                self.main.ProbeProtocolError,
                "第二次进入活动",
            ):
                self.main.enter_activity(re_enter=True, max_retries=1)

        package_name = self.main.GAME_PACKAGE_NAME
        self.assertNotIn(("close_app", package_name), self.adb.calls)
        self.assertNotIn(("open_app", package_name), self.adb.calls)
        self.assertNotIn(("disable_weak_network", package_name), self.adb.calls)

    def test_cleanup_keeps_drop_when_probe_request_may_be_pending(self):
        transaction = self.main.ProbeTransaction(level=1, cell=(0, 0), index=0)
        transaction.advance(self.main.ProbePhase.REQUEST_PENDING)
        self.main._active_probe = transaction

        self.main.cleanup_weak_network("测试清理")

        package_name = self.main.GAME_PACKAGE_NAME
        self.assertNotIn(("disable_weak_network", package_name), self.adb.calls)
        self.assertFalse(self.main._weak_network_cleanup_done)

    def test_probe_transaction_preserves_network_order(self):
        waits = iter(
            [
                DummyMatch((1, 1)),  # 点击前已在详情页
                DummyMatch((10, 20)),  # 第二次进入：活动按钮
                DummyMatch((30, 40)),  # 第二次进入：详情页
                DummyMatch((50, 60)),  # REJECT 后的重试按钮
                DummyMatch((70, 80)),  # 登录后下一轮：活动按钮
                DummyMatch((90, 100)),  # 登录后下一轮：详情页
            ]
        )
        hit_map = [[0, 0], [0, 0]]

        with (
            patch.object(
                self.main,
                "wait_until_occur",
                side_effect=lambda *args, **kwargs: next(waits),
            ),
            patch.object(self.main, "click_template", return_value=True),
            patch.object(self.main, "is_diamond_hit", return_value=True),
        ):
            result = self.main._probe_cell(
                level=1,
                hit_map=hit_map,
                cell=(0, 1),
                point=(400, 300),
                index=1,
            )

        package_name = self.main.GAME_PACKAGE_NAME
        network_calls = [
            call
            for call in self.adb.calls
            if call[0]
            in {
                "enable_reject_network",
                "disable_reject_network",
                "disable_weak_network",
                "enable_weak_network",
            }
        ]
        self.assertTrue(result)
        self.assertEqual(hit_map[0][1], 1)
        self.assertIsNone(self.main._active_probe)
        self.assertEqual(
            network_calls,
            [
                ("enable_reject_network", package_name),
                ("disable_reject_network", package_name),
                ("disable_weak_network", package_name),
                ("enable_weak_network", package_name),
            ],
        )

    def test_missing_retry_keeps_probe_pending_and_does_not_restore_drop(self):
        waits = iter(
            [
                DummyMatch((1, 1)),  # 点击前已在详情页
                DummyMatch((10, 20)),  # 第二次进入：活动按钮
                DummyMatch((30, 40)),  # 第二次进入：详情页
                None,  # REJECT 后没有重试按钮
            ]
        )
        hit_map = [[0, 0], [0, 0]]

        with (
            patch.object(
                self.main,
                "wait_until_occur",
                side_effect=lambda *args, **kwargs: next(waits),
            ),
            patch.object(self.main, "click_template", return_value=True),
            patch.object(self.main, "is_diamond_hit", return_value=False),
        ):
            with self.assertRaisesRegex(
                self.main.ProbeProtocolError,
                "未出现重试按钮",
            ):
                self.main._probe_cell(
                    level=1,
                    hit_map=hit_map,
                    cell=(0, 1),
                    point=(400, 300),
                    index=1,
                )

        package_name = self.main.GAME_PACKAGE_NAME
        self.assertIsNotNone(self.main._active_probe)
        self.assertTrue(self.main._active_probe.request_may_be_pending)
        self.assertIn(("enable_reject_network", package_name), self.adb.calls)
        self.assertNotIn(("disable_weak_network", package_name), self.adb.calls)

    def test_preflight_failure_retries_the_same_cell(self):
        hit_map = [[0, 0], [0, 0]]

        with (
            patch.object(
                self.main,
                "_execute_probe_transaction",
                side_effect=[
                    self.main.ProbeNotReadyError("页面未准备好"),
                    False,
                ],
            ) as execute,
            patch.object(self.main, "enter_activity") as recover,
        ):
            result = self.main._probe_cell(
                level=1,
                hit_map=hit_map,
                cell=(0, 1),
                point=(400, 300),
                index=1,
            )

        self.assertFalse(result)
        self.assertEqual(execute.call_count, 2)
        self.assertEqual(execute.call_args_list[0], execute.call_args_list[1])
        recover.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
