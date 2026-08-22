FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=en_US.UTF-8 \
    LC_ALL=en_US.UTF-8

# 1. 安装系统工具与运行时库
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        bash \
        curl \
        wget \
        ca-certificates \
        git \
        build-essential \
        openssh-client \
        expect \
        zip \
        unzip \
        locales \
        tzdata \
        iputils-ping \
        # Office 转换
        libreoffice \
        pandoc \
        poppler-utils \
        # 图片/视频/媒体
        imagemagick \
        ffmpeg \
        # OCR
        tesseract-ocr \
        tesseract-ocr-chi-sim \
        tesseract-ocr-chi-tra \
        # 网页截图 / 自动化
        chromium \
        # 开发辅助工具
        jq \
        ripgrep \
        fd-find \
        tree \
        # 字体
        fonts-noto-cjk \
        fonts-noto-color-emoji \
        fonts-liberation && \
    sed -i 's/# en_US.UTF-8/en_US.UTF-8/' /etc/locale.gen && \
    locale-gen && \
    rm -rf /var/lib/apt/lists/*

# 2. 允许 ImageMagick 处理 PDF（默认被安全策略禁止）
RUN if [ -f /etc/ImageMagick-6/policy.xml ]; then \
        sed -i 's/<policy domain="coder" rights="none" pattern="PDF" \/>/<policy domain="coder" rights="read|write" pattern="PDF" \/>/g' /etc/ImageMagick-6/policy.xml; \
    fi

# 3. 安装 Node.js 20 LTS（复用 curl，已在上方安装）
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/* && \
    node --version && \
    npm --version

# 3.5 全局安装 office skill 需要的 Node 包
RUN npm install -g docx pptxgenjs && \
    npm cache clean --force

WORKDIR /opt/workspace

COPY docker/toolbox-requirements.txt /tmp/toolbox-requirements.txt

# 4. 创建 Python 虚拟环境并安装依赖
RUN python -m venv /opt/agent-venv && \
    /opt/agent-venv/bin/pip install --no-cache-dir --upgrade pip && \
    /opt/agent-venv/bin/pip install --no-cache-dir -r /tmp/toolbox-requirements.txt && \
    rm -f /tmp/toolbox-requirements.txt

ENV AGENT_TOOLBOX_VENV=/opt/agent-venv
ENV PATH="/opt/agent-venv/bin:${PATH}"
ENV NODE_PATH="/usr/lib/node_modules"

