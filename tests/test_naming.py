import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from fetch_video import (detect_platform, extract_url, parse_share_nickname,
                         sanitize, stamp)


class NamingTests(unittest.TestCase):
    def test_sanitize_removes_illegal_chars(self):
        self.assertEqual(sanitize('a/b:c*d?"e<f>g|h'), "abcdefgh")

    def test_sanitize_fallback(self):
        self.assertEqual(sanitize("///"), "未知")

    def test_extract_url_from_share_text(self):
        text = "6.98 复制打开抖音，看看【xx的作品】 https://v.douyin.com/iRNBh/ 看视频"
        self.assertEqual(extract_url(text), "https://v.douyin.com/iRNBh/")

    def test_parse_share_nickname_xiaohongshu(self):
        text = ("46 【AI根本自动化不了  - 阿张正传 | 小红书 - 你的生活兴趣社区】 "
                "😆 2teTOnglq6C1JrT 😆 https://www.xiaohongshu.com/discovery/item/xxx")
        self.assertEqual(parse_share_nickname(text), "阿张正传")

    def test_parse_share_nickname_douyin_no_nickname(self):
        text = "6.98 复制打开抖音，看看【xx的作品】 https://v.douyin.com/iRNBh/ 看视频"
        self.assertIsNone(parse_share_nickname(text))

    def test_parse_share_nickname_rejects_hex_id(self):
        text = "【标题 - 62bda1f1000000001b02775b | 小红书 - 你的生活兴趣社区】 https://xhslink.com/abc"
        self.assertIsNone(parse_share_nickname(text))

    def test_detect_platform(self):
        self.assertEqual(detect_platform("https://v.douyin.com/iRNBh/"), "douyin")
        self.assertEqual(detect_platform("https://b23.tv/abc"), "bilibili")
        self.assertEqual(detect_platform("https://xhslink.com/abc"), "xiaohongshu")
        self.assertEqual(detect_platform("https://example.com"), "unknown")

    def test_stamp(self):
        self.assertEqual(stamp(65), "01:05")
        self.assertEqual(stamp(3671), "01:01:11")


if __name__ == "__main__":
    unittest.main()
