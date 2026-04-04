FROM python:3.11-slim

WORKDIR /app

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用
COPY . .

# 初始化数据库
RUN python app.py & sleep 3 && kill %1 2>/dev/null || true

EXPOSE 5000

# 启动
CMD ["python", "app.py"]
