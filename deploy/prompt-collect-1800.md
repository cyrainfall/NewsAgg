NewsAgg 定时采集（18:00）。执行：

cd /opt/newsagg/repo && bash .agents/skills/newsagg-collect/scripts/run_all.sh --collect-only

这是纯脚本采集（不碰 git、不耗模型、同日结果累积合并）。完成后用一两句话报告：候选条数、每次采到多少新条目、失败源、是否有陈旧源。若失败请贴出错误原文。
