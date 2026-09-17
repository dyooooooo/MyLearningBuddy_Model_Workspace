# 高中示意图：免费开源显示工具与网上示例

> **读者**：选型、出题/讲解配图。  
> **范围**：语文、数学、英语、化学、物理、生物。只列可自托管的免费开源工具。  
> **对照**：DeepTutor v1.6.4 自带 SVG / Mermaid / Chart.js / HTML / Manim，可选官网 GeoGebra（产品里不建议用）。公式继续用已有的 MathJax。

先点「主推示例」看观感；备选和总表在后面。

---

## 1. 不要用的（商用或官网封闭服务）

| 排除 | 原因 |
|------|------|
| Desmos 接口 | 产品嵌入要付费 |
| BioRender | 商用插画 SaaS |
| ChemDraw / Marvin 商用版 | 收费化学编辑器 |
| GeoGebra 官网应用 / `geogebra.org` 脚本 | 网页服务有非商限制 |
| PhET | 只能播它自己的仿真，不是任意出图库 |

---

## 2. 先看效果（按科点开）

### 数学 — 平面几何 / 坐标系 / 函数

主推 **JSXGraph**（MIT / LGPL，可只选 MIT）。点线圆是约束算出来的，不是估像素。

| 看什么 | 链接 |
|--------|------|
| 官方示例库（286 个，可拖） | https://jsxgraph.org/wiki/index.php/Category:Examples |
| 函数图像 | https://jsxgraph.org/wiki/index.php/Function_graph |
| 简单函数绘图器 | https://jsxgraph.org/share/example/simple-function-plotter |
| 割线与切线 | https://jsxgraph.uni-bayreuth.de/share/example/secant-and-tangent-on-function-graph |
| 欧拉线（高中平面几何） | https://jsxgraph.org/wiki/index.php/Euler_line |
| 泰勒斯定理 | https://jsxgraph.org/wiki/index.php/Thales_theorem |
| 正弦 / 余弦 | https://jsxgraph.org/wiki/index.php/Sine_and_cosine |
| 球的平行投影（立体感） | https://jsxgraph.org/wiki/index.php/Parallel_projection_of_a_sphere |

备选 **CindyJS**（Apache）：https://cindyjs.org/gallery/main/

### 数学 — 试卷印刷 / 立体几何投影

主推 **TikZ + tkz-euclide**（LaTeX 工程公共许可），编译器用 **Tectonic**（MIT）。立体印刷用 **Asymptote**（LGPL）。

| 看什么 | 链接 |
|--------|------|
| TikZ 示例站（教材风矢量图） | https://tikz.net/ |
| TeXample 数学图 | https://texample.net/tikz/examples/area/mathematics/ |
| TeXample 几何 | https://texample.net/tikz/examples/tag/geometry/ |
| Asymptote 三维画廊 | https://asymptote.sourceforge.io/gallery/ |

备选 Typst + CeTZ：https://typst.app/universe/package/cetz

### 数学 — 讲解动画

复用 DeepTutor 已有的 **Manim**（MIT）。只做动画，不当试卷底图。

| 看什么 | 链接 |
|--------|------|
| Manim Community 官方示例 | https://docs.manim.community/en/stable/examples.html |
| 画廊（社区） | https://docs.manim.community/en/stable/examples.html#basicanimations |

### 化学 — 结构式 / 化学键

主推 **RDKit 出 SVG**（BSD）；要在页上改结构再加 **Ketcher**（Apache）。

| 看什么 | 链接 |
|--------|------|
| Ketcher 在线编辑器（画苯环、反应） | https://lifescience.opensource.epam.com/ketcher/demo.html |
| RDKit 画分子入门（结构式效果） | https://www.rdkit.org/docs/GettingStartedInPython.html#drawing-molecules |
| RDKit 菜谱（更多排版） | https://www.rdkit.org/docs/Cookbook.html |

### 化学 / 生物 — 三维分子

主推 **3Dmol.js**（BSD，高中够用、包小）。蛋白质级备选 **Mol\***（MIT）。

| 看什么 | 链接 |
|--------|------|
| 3Dmol 嵌入示例（可转） | https://3dmol.org/doc/tutorial-embeddable.html |
| 3Dmol 在线查看器（输入 PDB 号，如 `1YCR`） | https://3dmol.org/viewer.html |
| Mol\* 官方查看器 | https://molstar.org/viewer/ |
| Mol\* 组件演示 | https://molstar.org/molstar-components/ |

### 物理 — 受力 / 光路 / 场

没有「又好看又能任意出题」的交互引擎。教材级走 **TikZ**；交互矢量可用 JSXGraph。

| 看什么 | 链接 |
|--------|------|
| TikZ 物理/力学图 | https://tikz.net/tag/physics/ |
| TeXample 物理 | https://texample.net/tikz/examples/area/physics/ |
| JSXGraph 单摆 | https://jsxgraph.org/wiki/index.php/Pendulum |
| JSXGraph 抛体 | https://jsxgraph.org/wiki/index.php/Cannonball |
| JSXGraph 折射（斯涅尔定律） | https://jsxgraph.org/wiki/index.php/Snell's_law |

### 物理 — 电路

印刷主推 **CircuiTikZ**（LPPL / GPL），网表可用 **lcapy**。可点仿真用 **CircuitJS**（GPL-2，须隔离页面）。

| 看什么 | 链接 |
|--------|------|
| CircuiTikZ 手册里的电路图 | https://circuitikz.github.io/circuitikz/ |
| TeXample 电路 | https://texample.net/tikz/examples/tag/circuitikz/ |
| CircuitJS 在线仿真（开源作者站） | https://www.falstad.com/circuit/circuitjs.html |
| lcapy 文档（网表 → 原理图） | https://lcapy.readthedocs.io/ |

### 生物 — 细胞 / 器官 / 通路

细胞器是插画，不是几何题。用 **Bioicons + Servier Medical Art**（知识共享许可）拼可缩放矢量图（SVG）。通路用 **Cytoscape.js**（MIT）。

| 看什么 | 链接 |
|--------|------|
| Bioicons 图标库 | https://bioicons.com/ |
| Servier 医学插画 | https://smart.servier.com/ |
| Cytoscape.js 演示 | https://js.cytoscape.org/#demos |

### 语文 — 层次 / 人物关系 / 思维导图

关系图 **AntV G6**（MIT）；脑图 **Markmap**（MIT）；流程继续 DeepTutor 的 **Mermaid**。

| 看什么 | 链接 |
|--------|------|
| G6 示例（树、关系、脑图风） | https://g6.antv.antgroup.com/examples |
| Markmap 在线试用（左侧写 Markdown） | https://markmap.js.org/repl |
| Mermaid 在线编辑 | https://mermaid.live/ |

### 英语 — 句法树

主推 **spaCy displaCy**（MIT）。语篇结构复用 G6。

| 看什么 | 链接 |
|--------|------|
| displaCy 依存树演示 | https://explosion.ai/demos/displacy |
| 官方说明（含实体高亮） | https://spacy.io/usage/visualizers |

### 公式 / 化学方程式（不是示意图）

复用已有 **MathJax + mhchem**。

| 看什么 | 链接 |
|--------|------|
| MathJax 官网演示 | https://www.mathjax.org/#demo |
| mhchem 化学式 | https://mhchem.github.io/MathJax-mhchem/ |

---

## 3. 选型总表

| 需求 | 风险 | 结论 | 主推 | 备选 | 不用 |
|------|------|------|------|------|------|
| 数学：平面几何、坐标系、函数 | 高 | 集成 | JSXGraph | CindyJS | 模型画 SVG；官网 GeoGebra |
| 数学：试卷印刷 / 立体投影 | 高 | 集成 | TikZ + tkz-euclide + Tectonic；立体 Asymptote | Typst + CeTZ | Manim 批量出卷 |
| 数学：讲解动画 | 高 | 复用 | Manim | — | 当日常底图 |
| 化学：结构式、化学键 | 高 | 集成 | RDKit 出 SVG；编辑用 Ketcher | Indigo | 手画键 |
| 化学 / 生物：三维分子 | 高 | 集成 | 3Dmol.js | Mol\* | 自研 WebGL |
| 物理：受力、光路、场 | 高 | 集成 | TikZ 风格包 | JSXGraph 矢量 | Matter.js 游戏风 |
| 物理：电路 | 高 | 集成 | CircuiTikZ + lcapy | CircuitJS（GPL-2，隔离） | 手画元件 |
| 生物：细胞 / 器官剖面 | 高 | 集成素材 | Bioicons + Servier | Cytoscape.js 通路 | 指望引擎「生成」解剖图 |
| 语文：层次、人物、脑图 | 中 | 集成 | G6；Markmap | Mermaid 流程 | 模型手排框 |
| 英语：句法树 | 中 | 集成 | displaCy | 语篇复用 G6 | 手画树 |
| 公式 / 化学方程式 | 低 | 复用 | MathJax + mhchem | — | 当示意图用 |

---

## 4. 和现有系统的关系

```text
题干 → 该科描述（JSXGraph 命令 / SMILES / 受力对象 / 句法树 / 节点边）
     → 开源引擎算出图
     → 风格包（教材线、虚辅助线、无工具栏）
     → SVG 或 iframe
```

DeepTutor v1.6.4 的可视化插件位（ZIP + 沙箱 iframe）可以挂 JSXGraph、Ketcher/RDKit 预览、3Dmol、G6。**不要再让模型直接画理科 SVG。** 官网 GeoGebra 捆绑项建议关掉。

CircuitJS 是 GPL-2，只能单独页面，不要链进主应用许可证。
