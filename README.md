# cenXiv 项目

## 项目介绍

cenXiv 是一个基于 Django 的电子预印本存档，旨在为用户提供中文(CN)和英文(EN)双语界面的 arXiv 论文浏览、搜索和下载功能。该项目允许用户浏览、搜索和获取科学论文的各种格式，包括中文全文 PDF、英文全文 PDF、HTML、DVI 和源文件等。

![首页截图](https://github.com/cenXiv/cenxiv/blob/main/pictures/cenXiv_home.jpg)

## 安装和运行

### 前提条件

确保您的系统上已安装以下软件：

- **Python 3.13** 或更高版本
- **Poetry**（用于依赖管理和虚拟环境）
- **Git**（用于代码版本控制）

### 安装步骤

1. **克隆项目到本地**

   ```bash
   git clone https://github.com/cenXiv/cenxiv.git
   cd cenxiv
   ```

2. **安装 Poetry**

   如果您尚未安装 Poetry，可以使用以下命令进行安装：

   ```bash
   curl -sSL https://install.python-poetry.org | python3 -
   ```

   或参考 [Poetry 官方文档](https://python-poetry.org/docs/#installation) 进行安装。

3. **使用 Poetry 创建虚拟环境并安装依赖**

   ```bash
   poetry install
   ```

   这将根据 `pyproject.toml` 文件安装所有必要的依赖并创建虚拟环境。

4. **激活虚拟环境**

   ```bash
   poetry shell
   ```

5. **配置环境变量**

   创建一个 `.env` 文件在项目根目录，并添加以下内容：

   ```dotenv
   SECRET_KEY=your_secret_key
   DEBUG=True
   ALLOWED_HOSTS=localhost,127.0.0.1
   ```

   将 `your_secret_key` 替换为您的 Django 密钥。

6. **运行数据库迁移**

   ```bash
   python manage.py migrate
   ```

7. **创建超级用户（可选）**

   ```bash
   python manage.py createsuperuser
   ```

8. **启动开发服务器**

   ```bash
   python manage.py runserver
   ```

9. **访问项目**

   在浏览器中访问 `http://127.0.0.1:8000/` 查看 cenXiv 项目。

## TODO 列表

- [ ] **实现页面缓存功能**
- [ ] **实现论文的中文翻译及中文全文 PDF 下载**
- [ ] **实现用户注册和登录功能**
- [ ] **编写单元测试和集成测试**
- [ ] **完善文档**

## 贡献

欢迎任何人对 cenXiv 项目进行贡献！无论是报告问题、提出建议还是提交代码，您的参与都将使项目更加完善。请遵循以下步骤进行贡献：

1. **Fork 本项目**

2. **创建您的特性分支**

   ```bash
   git checkout -b feature/YourFeature
   ```

3. **提交您的更改**

   ```bash
   git commit -m '添加了某个新功能'
   ```

4. **推送到分支**

   ```bash
   git push origin feature/YourFeature
   ```

5. **创建一个 Pull Request**

   在 GitHub 上发起一个新的 Pull Request，我们的维护团队会尽快审查您的更改。

### 贡献指南

- **编码规范**：请遵循 PEP 8 编码风格。
- **文档**：请为您的功能添加相应的文档说明。
- **测试**：确保所有新功能都有相应的测试覆盖。
- **提交信息**：请使用清晰、简洁的提交信息，描述您所做的更改。

## 联系我们

如有任何问题或建议，请通过 GitHub 的 Issues 页面提交，或联系项目维护者：

- **邮箱**：sfzuo@bao.ac.com
- **GitHub Discussions**：请访问 [cenXiv Discussions](https://github.com/cenXiv/cenxiv/discussions)

感谢您的支持与贡献！

## 技术栈

- **后端**：Django 5.1.4
- **前端**：HTML, CSS, JavaScript
- **数据库**：SQLite（开发），建议使用 PostgreSQL（生产）
- **依赖管理**：Poetry
- **版本控制**：Git

## 许可证

本项目暂无许可证。