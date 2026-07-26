# Sync exam-ocr-server from WSL to Spark

Spark cannot access WSL paths directly. Run this **from WSL** (not on Spark):

```bash
rsync -avz --exclude '.gradle' --exclude 'build' --exclude 'node_modules' \
  /mnt/d/01_coding/LearningStack/exam-ocr-server/ \
  mylearningbuddy@192.168.1.10:~/MyLearningBuddy_Model_Workspace/exam-ocr-server/
```

After sync, on Spark rebuild the gateway:

```bash
cd ~/MyLearningBuddy_Model_Workspace/learning-buddy-c2
```

```bash
docker compose build c2-gateway
```

```bash
docker compose up -d c2-gateway
```

**Note:** A minimal Python/FastAPI scaffold is already in `~/MyLearningBuddy_Model_Workspace/exam-ocr-server` so deployment can proceed before rsync. Replace it with the real exam-ocr-server source when available.
