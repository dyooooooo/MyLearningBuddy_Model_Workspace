# Triple Check Report: e832287d-9b1c-41a3-94f4-a6407a9bf47e

- label: 35B_v2_coverage_test_r3
- status: chunked
- prompt: layout_blueprint_v2
- coverage: {"total_blocks": 174, "rendered_blocks": 174, "coverage": 1.0, "pages_with_drops": []}

| 页 | 原图文本块 | JSON blocks(kind/zone) | HTML | 对齐 | 问题 |
|---:|---:|---|---|---|---|
| 1 | 5 | kind={'figure': 2, 'text': 6}; zone={'main': 8} | ids=8; grid,aside,figure | pdf→json=0.76; pdf→html=0.76; cov=1.0 | OK |
| 2 | 0 | kind={'text': 1, 'figure': 1}; zone={'main': 2} | ids=2; grid,aside,figure | pdf→json=1.0; pdf→html=1.0; cov=1.0 | OK |
| 3 | 27 | kind={'table': 10}; zone={'main': 10} | ids=10; grid,aside,table | pdf→json=1.0; pdf→html=0.3477; cov=1.0 | PDF→HTML 文本召回偏低 0.3477<br>原图有较多文本，但 JSON 无 text 块 |
| 4 | 4 | kind={'running': 2, 'text': 4}; zone={'running': 2, 'main': 4} | ids=4; grid,aside | pdf→json=0.9388; pdf→html=0.9388; cov=1.0 | OK |
| 5 | 4 | kind={'running': 2, 'text': 4}; zone={'running': 2, 'main': 4} | ids=4; grid,aside | pdf→json=1.0; pdf→html=1.0; cov=1.0 | OK |
| 6 | 10 | kind={'running': 2, 'table': 1}; zone={'running': 2, 'main': 1} | ids=1; grid,aside,table | pdf→json=1.0; pdf→html=1.0; cov=1.0 | 原图有较多文本，但 JSON 无 text 块 |
| 7 | 5 | kind={'running': 2, 'text': 10}; zone={'running': 2, 'main': 10} | ids=10; grid,aside | pdf→json=1.0; pdf→html=1.0; cov=1.0 | OK |
| 8 | 3 | kind={'figure': 1, 'text': 3}; zone={'main': 4} | ids=4; grid,aside,figure | pdf→json=1.0; pdf→html=1.0; cov=1.0 | OK |
| 9 | 8 | kind={'running': 2, 'text': 12, 'figure': 1}; zone={'running': 2, 'main': 12, 'aside_left': 1} | ids=13; grid,aside,math,figure | pdf→json=0.9494; pdf→html=0.9476; cov=1.0 | OK |
| 10 | 5 | kind={'running': 2, 'table': 6}; zone={'running': 2, 'main': 6} | ids=6; grid,aside,table | pdf→json=0.964; pdf→html=0.2216; cov=1.0 | PDF→HTML 文本召回偏低 0.2216<br>原图有较多文本，但 JSON 无 text 块 |
| 11 | 7 | kind={'text': 7, 'figure': 1, 'equation': 1, 'running': 2}; zone={'main': 8, 'aside_left': 1, 'running': 2} | ids=9; grid,aside,math,figure | pdf→json=0.9527; pdf→html=0.9436; cov=1.0 | OK |
| 12 | 6 | kind={'running': 2, 'table': 10, 'equation': 1, 'figure': 2}; zone={'running': 2, 'main': 12, 'aside_right': 1} | ids=13; grid,aside,table,figure | pdf→json=0.8409; pdf→html=0.057; cov=1.0 | PDF→HTML 文本召回偏低 0.057<br>JSON 有 equation，但 HTML 未见 data-latex<br>原图有较多文本，但 JSON 无 text 块 |
| 13 | 8 | kind={'running': 2, 'text': 7, 'equation': 1}; zone={'running': 2, 'aside_left': 1, 'main': 7} | ids=8; grid,aside,math | pdf→json=0.9009; pdf→html=0.8877; cov=1.0 | OK |
| 14 | 7 | kind={'running': 2, 'table': 6, 'figure': 1}; zone={'running': 2, 'main': 5, 'aside_right': 2} | ids=7; grid,aside,table,figure,cases | pdf→json=0.7735; pdf→html=0.2857; cov=1.0 | PDF→HTML 文本召回偏低 0.2857<br>原图有较多文本，但 JSON 无 text 块 |
| 15 | 8 | kind={'running': 2, 'text': 18, 'equation': 1}; zone={'running': 2, 'main': 18, 'aside_left': 1} | ids=19; grid,aside,math | pdf→json=0.8291; pdf→html=0.8255; cov=1.0 | OK |
| 16 | 9 | kind={'running': 2, 'table': 12, 'figure': 2, 'equation': 3}; zone={'running': 2, 'main': 14, 'aside_right': 3} | ids=17; grid,aside,table,figure | pdf→json=0.8188; pdf→html=0.1256; cov=1.0 | PDF→HTML 文本召回偏低 0.1256<br>JSON 有 equation，但 HTML 未见 data-latex<br>原图有较多文本，但 JSON 无 text 块 |
| 17 | 7 | kind={'running': 2, 'table': 13, 'figure': 1, 'equation': 1}; zone={'running': 2, 'main': 15} | ids=15; grid,aside,table,figure | pdf→json=0.8424; pdf→html=0.1187; cov=1.0 | PDF→HTML 文本召回偏低 0.1187<br>JSON 有 equation，但 HTML 未见 data-latex<br>原图有较多文本，但 JSON 无 text 块 |
| 18 | 9 | kind={'running': 2, 'equation': 1, 'table': 2}; zone={'running': 2, 'main': 3} | ids=3; grid,aside,table | pdf→json=0.6361; pdf→html=0.3079; cov=1.0 | PDF→JSON 文本召回偏低 0.6361<br>PDF→HTML 文本召回偏低 0.3079<br>JSON 有 equation，但 HTML 未见 data-latex<br>原图有较多文本，但 JSON 无 text 块 |
| 19 | 5 | kind={'running': 2, 'text': 12}; zone={'running': 2, 'main': 12} | ids=12; grid,aside,math | pdf→json=0.6917; pdf→html=0.6722; cov=1.0 | OK |
| 20 | 6 | kind={'running': 2, 'text': 9}; zone={'running': 2, 'main': 9} | ids=9; grid,aside,math | pdf→json=0.9859; pdf→html=0.9859; cov=1.0 | OK |