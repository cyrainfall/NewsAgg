# -*- coding: utf-8 -*-
"""systemd 单元的正确性（ADR-0077）。

## 为什么要有这个测试

2026-10-27 第一次在服务器上装定时器，四个 unit 里有**三个是坏的**，而且
两个错都是「手写四份再复制粘贴」特有的：

1. `OnCalendar=*-*-* 0800:00:00` —— systemd 只认 `HH:MM:SS`，小时必须是两位。
   报 `Failed to parse calendar specification`，于是 timer **完全装不上**
   （`systemctl enable --now` 直接失败）。
2. 13:00 与 18:00 两个 service 的 `ExecStart` 都指向
   `prompt-collect-0800.md` —— 复制粘贴时漏改。

第 2 个尤其阴险：它**不会报错**，只会让 13:00 和 18:00 跑成 08:00 的活，
而采集是幂等的（同日累积合并），所以结果看起来完全正常。

现在单元由 `deploy/gen-units.sh` 生成，本测试钉住生成结果。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEPLOY = REPO / "deploy"

EXPECTED_TIMES = {
    "newsagg-collect-0800": "08:00",
    "newsagg-collect-1300": "13:00",
    "newsagg-collect-1800": "18:00",
    "newsagg-main-pipeline": "21:00",
}


def _read(name: str) -> str:
    return (DEPLOY / name).read_text(encoding="utf-8")


class TimerOnCalendarTest(unittest.TestCase):
    def test_oncalendar_uses_two_digit_hour(self):
        """systemd 的 OnCalendar 只认 HH:MM:SS，小时必须两位。"""
        for tag in EXPECTED_TIMES:
            text = _read(f"{tag}.timer")
            m = re.search(r"^OnCalendar=(.+)$", text, re.M)
            self.assertIsNotNone(m, f"{tag}.timer 没有 OnCalendar")
            value = m.group(1).strip()
            self.assertRegex(
                value, r"^\*-\*-\* \d{2}:\d{2}:\d{2}$",
                f"{tag}.timer 的 OnCalendar 形式不对：{value!r}"
                "（小时必须是两位，如 08:00:00）")

    def test_each_timer_points_at_its_own_service(self):
        for tag in EXPECTED_TIMES:
            text = _read(f"{tag}.timer")
            self.assertIn(f"Unit={tag}.service", text)

    def test_persistent_is_on(self):
        """服务器重启后要补跑错过的任务。"""
        for tag in EXPECTED_TIMES:
            self.assertIn("Persistent=true", _read(f"{tag}.timer"))


class ServiceExecStartTest(unittest.TestCase):
    def test_execstart_prompt_matches_the_tag(self):
        """每个 service 跑自己的 prompt——不能全指向 0800 那份。

        只对采集任务做「文件名里的时刻 == tag 里的时刻」这条检查：主流程的
        prompt 叫 `prompt-main-pipeline.md`，它不是按时刻命名的。
        """
        for tag in EXPECTED_TIMES:
            text = _read(f"{tag}.service")
            m = re.search(r"^ExecStart=.*run-prompt\.sh (\S+)$", text, re.M)
            self.assertIsNotNone(m, f"{tag}.service 的 ExecStart 格式不对")
            prompt = Path(m.group(1)).name
            if tag.startswith("newsagg-collect-"):
                # 采集的 prompt 按时刻命名：时刻必须对得上
                hhmm = EXPECTED_TIMES[tag].replace(":", "")
                self.assertIn(
                    hhmm, prompt,
                    f"{tag}.service 跑的 prompt 是 {prompt}，与它自己的时刻"
                    f"（{EXPECTED_TIMES[tag]}）对不上——这正是 13:00/18:00 "
                    "都跑成 08:00 那个 bug")
            else:
                self.assertEqual("prompt-main-pipeline.md", prompt)

    def test_prompt_files_exist(self):
        for tag in EXPECTED_TIMES:
            text = _read(f"{tag}.service")
            m = re.search(r"^ExecStart=.*run-prompt\.sh (\S+)$", text, re.M)
            self.assertIsNotNone(m)
            # 服务器上的路径；本地仓里对应 deploy/ 下的同名文件
            local = DEPLOY / Path(m.group(1)).name
            self.assertTrue(local.exists(), f"{local} 不存在")

    def test_credential_file_is_referenced(self):
        """headless 不读 DSH 凭证库，必须显式给（ADR-0077）。"""
        for tag in EXPECTED_TIMES:
            self.assertIn(
                "EnvironmentFile=-/etc/newsagg/dsh-credentials.env",
                _read(f"{tag}.service"),
                f"{tag}.service 没有引用凭证文件 → dsh headless 会报 MISSING_CREDENTIAL")

    def test_working_directory_is_the_repo(self):
        for tag in EXPECTED_TIMES:
            self.assertIn("WorkingDirectory=/opt/newsagg/repo",
                          _read(f"{tag}.service"))


class GeneratorSelfCheckTest(unittest.TestCase):
    def test_generator_self_check_passes(self):
        """生成器自带的自检必须通过（时刻格式 + prompt 指向）。"""
        import subprocess

        proc = subprocess.run(
            ["bash", str(DEPLOY / "gen-units.sh")],
            capture_output=True, text=True, cwd=str(REPO))
        self.assertEqual(0, proc.returncode,
                         f"gen-units.sh 自检没过：\n{proc.stdout}\n{proc.stderr}")


if __name__ == "__main__":
    unittest.main()
