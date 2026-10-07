# AI HOT 日报自动站

把 AI HOT 每日日报自动抓取、渲染成单文件 HTML 晨报仪表盘,可部署到自己的服务器或 GitHub Pages。

## 特点

- **零依赖**:只用 Python 标准库,服务器上不需要 `pip install` 任何东西
- **自动回退**:当日日报未生成时,自动取最近一期并在页面标注「最新一期」
- **五版块固定分组**:模型发布 / 产品发布 / 行业动态 / 论文研究 / 技巧与观点
- **自动去重**:同一事件被多家转载时合并(标题公共前缀判定)
- **全局连续编号**:跨版块不重置
- **北京时间人话格式**:页面不出现任何 ISO 时间串
- **归档索引**:`output/index.html` 自动汇总历史各期

## 本地运行

```bash
python build.py                          # 拉最新一期
python build.py --date 2026-10-07        # 指定日期
python build.py --out dist               # 自定义输出目录
```

产出两个文件:

```
output/2026-10-07.html    当期日报页
output/index.html         归档索引(自动扫描 output/ 下所有日期)
```

本地预览:

```bash
python -m http.server 8000 --directory output
```

## 部署方案 A:自己的服务器(推荐,完全可控)

```bash
# 1. 上传代码
scp -r aihot-site user@your-server:/opt/

# 2. 首次生成,确认无误
ssh user@your-server 'cd /opt/aihot-site && python3 build.py'

# 3. 注册定时任务(每天 08:10)
sudo cp /opt/aihot-site/aihot-daily.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now aihot-daily.timer 2>/dev/null || true
sudo systemctl enable aihot-daily.service

# 4. 用 timer 触发定时(若上面 timer 不存在则手动建)
sudo tee /etc/systemd/system/aihot-daily.timer > /dev/null <<'EOF'
[Unit]
Description=每天触发 AI HOT 日报生成
[Timer]
OnCalendar=*-*-* 08:10:00
Persistent=true
[Install]
WantedBy=timers.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now aihot-daily.timer

# 5. 查看日志
journalctl -u aihot-daily.service -n 50 --no-pager
```

Nginx 配置示例:

```nginx
server {
    listen 80;
    server_name your-domain.com;
    root /opt/aihot-site/output;
    index index.html;

    location / {
        try_files $uri $uri/ $uri.html =404;
    }
}
```

改完执行 `sudo nginx -s reload`。

## 部署方案 B:GitHub Pages(免费,免服务器)

1. 把本目录推到 GitHub 仓库
2. 仓库 Settings → Pages → Source 选 **GitHub Actions**
3. 完成后访问 `https://<用户名>.github.io/<仓库名>/`,即归档索引;`/<仓库名>/YYYY-MM-DD.html` 是某一期日报
4. 手动触发:Actions 页面点 **Run workflow**

工作流已配好 Pages 发布链路,首次推送后需在 Settings → Pages 把 Source 手动设为 GitHub Actions(新建仓库时这个选项不会自动打开)。

想保留历史各期,需要把 `output/` 一起提交;若只靠 Actions 发布,产物只存在于 artifact 和 Pages 上,历史页面不会累积。想要归档累积的话,改用下面的定时提交变体:

```yaml
      - name: 提交变更
        run: |
          git config user.name  "aihot-bot"
          git config user.email "aihot-bot@users.noreply.github.com"
          git add output/
          git diff --staged --quiet || git commit -m "chore: 更新日报"
          git push
```

注意:GitHub 仓库如果 60 天无活动会暂停定时任务,长期运行建议每月手动触发一次。

## 调整版块归类

快讯(flashes)不带版块信息,由 `build.py` 里的 `SECTION_KEYWORDS` 加权打分自动归类。改词表即可调整:

```python
SECTION_KEYWORDS = {
    "sec-model":    [("模型", 2), ("权重", 2), ...],
    "sec-product":  [("插件", 2), ("app", 2), ...],
    ...
}
```

格式是 `(关键词, 权重)`。得分最高者胜,同分按 `SECTIONS` 声明顺序取靠前者。改完直接重跑 `python build.py` 验证。

调 `SUMMARY_MAX` 可以改摘要最大字数(默认 60 字)。

## 域名提醒

AI HOT 已换到新域名 `aihot.news`,旧域名 `aihot.virxact.com` 将于 2026 年 10 月 31 日起停用。

`build.py` 顶部有 `API_BASE` 和 `CANONICAL` 两个常量,到期后把 `API_BASE` 改成:

```python
API_BASE = "https://aihot.news/api/v1"
```

路径、参数、返回结构都不变,只换域名。
