# Aymane Ait Dads — CV

## Contact

- **Email:** Aymane.Ait-dads@eurecom.fr
- **Phone:** +33 7 60 92 50 93
- **LinkedIn:** linkedin.com/in/aymane-ait-dads
- **Location:** Sophia Antipolis, France

## Professional Summary

Data Science Engineering student at EURECOM building production ML systems. Ranked #1 on EURECOM leaderboard for AI-generated image detection by fine-tuning CLIP ViT-L/14 (428M params) on 250K samples. Built end-to-end ML pipelines at Orange Maroc covering 100K+ telemetry records, reducing manual analysis time by ~60%. Strong in PyTorch, transformer fine-tuning, computer vision, NLP, and MLOps. Looking for a Data Science / ML internship where I can ship real systems.

## Work Experience

### Data Science Intern — Orange Maroc
**Jul 2024 – Aug 2024 | Casablanca, Morocco**

- Engineered an end-to-end ML pipeline on 100K+ network telemetry records using Random Forest + KMeans, identifying the top 3 network-quality drivers and reducing manual analysis time by ~60%.
- Delivered KMeans segmentation mapping 5 distinct user performance profiles to commercial service tiers, directly informing go-to-market decisions.
- Applied feature engineering, cross-validated model evaluation, and predictive maintenance modeling on live telemetry data.
- Independently owned the full pipeline from raw data ingestion to executive-ready Power BI dashboards presented to senior management.

## Projects

### Robust AI-Generated Image Detection
**EURECOM | 2026**

- Ranked #1 on private EURECOM leaderboard — fine-tuned CLIP ViT-L/14 (428M params) on 250K training samples spanning 25+ generator types (Stable Diffusion, DALL-E, Midjourney, GAN variants).
- Applied FP16 mixed precision with GradScaler for T4 GPU efficiency; designed 10-view test-time augmentation (TTA) for out-of-distribution-robust inference.
- Designed ablation experiments across epoch count, transformer block unfreezing depth (4/6/8 layers), learning rate schedules, and augmentation strategies; proved 1-epoch fine-tuning preserves generalization better than extended re-training.
- **Stack:** PyTorch · OpenCLIP · Hugging Face · FP16/GradScaler · TTA

### Anomalous Sound Detection in Industrial Equipment
**EURECOM | 2025**

- Built a transformer autoencoder for unsupervised fault detection on industrial audio (AUC 0.80) — no labeled anomalies required.
- Used Mel spectrograms with SpecAugment data augmentation; benchmarked Mel vs. MFCC feature representations.
- Evaluated on a DCASE-style protocol, matching state-of-the-art unsupervised baselines.
- **Stack:** PyTorch · Librosa · Mel spectrograms · SpecAugment

### Aerial Cactus Detection & Twitter Sentiment Analysis
**EURECOM | 2025**

- Achieved 99.8% accuracy (F1=0.999, AUC=1.0) on 17,500+ aerial images by benchmarking CNN, DenseNet121, and CNN-Transformer hybrid architectures.
- Built a complete NLP tweet-sentiment pipeline: tokenization → TF-IDF baseline → word2vec embeddings → transformer fine-tuning — improving accuracy by 12pp over baseline.
- **Stack:** PyTorch · TensorFlow · Scikit-learn · NLTK

## Education

### Engineering Degree in Data Science
**EURECOM | Sep 2023 – Present | Sophia Antipolis, France**

Relevant coursework: Machine Learning · Deep Learning · Computer Vision · NLP · Cloud Computing · Image Security · Statistics · Distributed Systems

### Preparatory Classes (CPGE) — Mathematics & Physics
**Ibn Timiya, Marrakech | Sep 2021 – Jun 2023**

Intensive 2-year program in mathematics and physics as preparation for competitive engineering school entrance exams.

## Skills

### ML Frameworks & Libraries
PyTorch · TensorFlow · OpenCLIP · Scikit-learn · Pandas · NumPy · Hugging Face Transformers · Matplotlib · Seaborn

### LLM & Deep Learning
Transformer fine-tuning (CLIP ViT-L/14) · Autoregressive models · TTA (test-time augmentation) · Ensemble methods · FP16 mixed precision · GradScaler · Autoencoders

### Data & Engineering
Python (advanced) · SQL · Git · Linux · Power BI · Feature engineering · MLOps · Data pipelines

### Languages
English (fluent) · French (fluent) · Arabic (native) · Spanish (intermediate)
