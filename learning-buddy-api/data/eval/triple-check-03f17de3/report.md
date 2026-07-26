# Triple Check Report: 03f17de3-73f4-4af9-a868-9dfe6649d5ba

- label: 35B_v3_schema_pipeline_test_r3
- status: chunked
- prompt: layout_blueprint_v3
- coverage: {"total_blocks": 128, "rendered_blocks": 128, "coverage": 1.0, "pages_with_drops": []}
- content_quality: {"html_block_coverage": {"total_blocks": 128, "rendered_blocks": 128, "coverage": 1.0, "pages_with_drops": []}, "polluted_payload_count": 0, "pages_with_payload_errors": [], "avg_json_to_html_token_recall": 0.9738, "pages_with_low_fidelity": [18]}

| 页 | 原图文本块 | JSON blocks(kind/zone) | HTML | 对齐 | 问题 |
|---:|---:|---|---|---|---|
| 1 | 5 | kind={'figure': 1}; zone={'main': 1}; polluted=0 | ids=1; grid,aside,figure | pdf→json=0.76; pdf→html=0.76; json→html=1.0; cov=1.0 | OK |
| 2 | 0 | kind={'running': 1, 'figure': 1, 'text': 5}; zone={'running': 1, 'main': 6}; polluted=0 | ids=6; grid,aside,figure | pdf→json=1.0; pdf→html=1.0; json→html=1.0; cov=1.0 | OK |
| 3 | 27 | kind={'text': 5, 'table': 1}; zone={'main': 6}; polluted=0 | ids=6; grid,aside,table | pdf→json=1.0; pdf→html=1.0; json→html=1.0; cov=1.0 | OK |
| 4 | 4 | kind={'running': 2, 'text': 4}; zone={'running': 2, 'main': 4}; polluted=0 | ids=4; grid,aside | pdf→json=1.0; pdf→html=1.0; json→html=1.0; cov=1.0 | OK |
| 5 | 4 | kind={'running': 2, 'text': 4}; zone={'running': 2, 'main': 4}; polluted=0 | ids=4; grid,aside | pdf→json=1.0; pdf→html=1.0; json→html=1.0; cov=1.0 | OK |
| 6 | 10 | kind={'running': 2, 'table': 1}; zone={'running': 2, 'main': 1}; polluted=0 | ids=1; grid,aside,table | pdf→json=1.0; pdf→html=1.0; json→html=1.0; cov=1.0 | OK |
| 7 | 5 | kind={'running': 2, 'text': 2}; zone={'running': 2, 'main': 2}; polluted=0 | ids=2; grid,aside | pdf→json=1.0; pdf→html=1.0; json→html=1.0; cov=1.0 | OK |
| 8 | 3 | kind={'figure': 1, 'text': 2}; zone={'main': 3}; polluted=0 | ids=3; grid,aside,figure | pdf→json=1.0; pdf→html=1.0; json→html=1.0; cov=1.0 | OK |
| 9 | 8 | kind={'running': 2, 'figure': 1, 'text': 12}; zone={'running': 2, 'aside_left': 1, 'main': 12}; polluted=0 | ids=13; grid,aside,math,figure | pdf→json=0.9494; pdf→html=0.9476; json→html=0.9917; cov=1.0 | OK |
| 10 | 5 | kind={'running': 2, 'text': 5, 'table': 1}; zone={'running': 2, 'main': 6}; polluted=0 | ids=6; grid,aside,math,table | pdf→json=0.964; pdf→html=0.9602; json→html=0.9858; cov=1.0 | OK |
| 11 | 7 | kind={'running': 2, 'text': 9, 'equation': 1}; zone={'running': 2, 'main': 9, 'aside_left': 1}; polluted=0 | ids=10; grid,aside,math | pdf→json=0.9527; pdf→html=0.9436; json→html=0.9639; cov=1.0 | OK |
| 12 | 6 | kind={'running': 2, 'text': 6, 'figure': 2}; zone={'running': 2, 'main': 7, 'aside_right': 1}; polluted=0 | ids=8; grid,aside,math,figure | pdf→json=0.8409; pdf→html=0.8153; json→html=0.9134; cov=1.0 | OK |
| 13 | 8 | kind={'running': 2, 'text': 4}; zone={'running': 2, 'aside_left': 1, 'main': 3}; polluted=0 | ids=4; grid,aside,math | pdf→json=0.9009; pdf→html=0.8877; json→html=0.9774; cov=1.0 | OK |
| 14 | 7 | kind={'running': 2, 'text': 9, 'figure': 1}; zone={'running': 2, 'main': 8, 'aside_right': 2}; polluted=0 | ids=10; grid,aside,math,figure | pdf→json=0.777; pdf→html=0.7735; json→html=0.996; cov=1.0 | OK |
| 15 | 8 | kind={'running': 2, 'text': 4}; zone={'running': 2, 'main': 3, 'aside_left': 1}; polluted=0 | ids=4; grid,aside,math | pdf→json=0.8255; pdf→html=0.8255; json→html=0.9548; cov=1.0 | OK |
| 16 | 9 | kind={'running': 2, 'text': 10, 'figure': 2}; zone={'running': 2, 'main': 9, 'aside_right': 3}; polluted=0 | ids=12; grid,aside,math,figure | pdf→json=0.8188; pdf→html=0.7995; json→html=0.9549; cov=1.0 | OK |
| 17 | 7 | kind={'running': 2, 'text': 9, 'figure': 1, 'equation': 1}; zone={'running': 2, 'main': 11}; polluted=0 | ids=11; grid,aside,math,figure | pdf→json=0.8696; pdf→html=0.8424; json→html=0.9677; cov=1.0 | OK |
| 18 | 9 | kind={'running': 2, 'text': 4, 'figure': 1}; zone={'running': 2, 'main': 5}; polluted=0 | ids=5; grid,aside,math,figure | pdf→json=0.7354; pdf→html=0.6718; json→html=0.8248; cov=1.0 | JSON→HTML 保真偏低 0.8248 |
| 19 | 5 | kind={'running': 2, 'text': 12}; zone={'running': 2, 'main': 12}; polluted=0 | ids=12; grid,aside,math | pdf→json=0.6917; pdf→html=0.6722; json→html=0.9538; cov=1.0 | OK |
| 20 | 6 | kind={'running': 2, 'figure': 1, 'text': 5}; zone={'running': 2, 'main': 6}; polluted=0 | ids=6; grid,aside,math,figure | pdf→json=0.9859; pdf→html=0.9859; json→html=0.9926; cov=1.0 | OK |