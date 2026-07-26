"""v4 判例库（35B 线自有，禁止被 MinerU 线 import）。

两速记忆的慢速层「判例法」：
- ``store``  —— 独立 Milvus collection 的向量索引（复用共享 embedder + 连接，
  但物理隔离于语料库 collection）。
- ``service`` —— 裁决落判例（record_case）+ finding 富化（enrich_findings，
  仅降权/补候选，永不静默抑制）。
- ``induction`` —— 离线归纳 Agent + 晋级门（提议供人批准，绝不自动改规则）。

全部能力默认关闭（``settings.import35b_caselaw_enabled`` / 激活门槛
``import35b_caselaw_min_cases``），未激活时降级为 no-op，不改现有行为。
"""
from app.import_35b.caselaw.service import enrich_findings, record_case

__all__ = ["enrich_findings", "record_case"]
