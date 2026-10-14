"""人类行为模拟参数（随机延迟、滚动间隔）。"""

import random
import time


def sleep_random(min_ms: int, max_ms: int) -> None:
    """随机延迟，模拟真人操作节奏。"""
    if max_ms <= min_ms:
        time.sleep(min_ms / 1000.0)
        return
    time.sleep(random.randint(min_ms, max_ms) / 1000.0)


def navigation_delay() -> None:
    """页面导航后的随机等待。"""
    sleep_random(1000, 2500)


def typing_delay(char: str) -> float:
    """单字符输入间隔（秒）：中文稍慢，ASCII 稍快。"""
    if ord(char) > 127:
        return random.uniform(0.06, 0.16)
    return random.uniform(0.03, 0.09)
