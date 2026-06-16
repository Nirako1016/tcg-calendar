# TCG 赛事日历生成器

上传 Excel → 自动生成 ICS 日历 → 发布订阅链接

## 功能

- 上传 Excel（xlsx/xls），自动解析 TCG 赛事信息
- 生成标准 ICS 日历文件，支持 Apple 日历 / Google 日历 / Outlook 订阅
- 一键推送到 GitHub Pages，获得永久订阅链接
- 事件格式：【TCG品类】【赛事名称】

## Excel 格式

| A列 | B列 | C列 | D列 | E列 |
|------|------|------|------|------|
| TCG品类 | 赛事名称 | 开始日期 | 结束日期 | 城市 |

第一行为标题行，从第二行开始读取数据。

## 本地运行

```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

打开 http://localhost:8000

## 部署

### Render（推荐）

1. 将代码推送到 GitHub
2. 在 Render 中新建 Web Service，连接仓库
3. 设置环境变量：`GITHUB_TOKEN`、`GITHUB_REPO`

### 环境变量

| 变量 | 说明 | 示例 |
|------|------|------|
| GITHUB_TOKEN | GitHub Personal Access Token | ghp_xxx |
| GITHUB_REPO | GitHub 仓库名 | username/repo |
