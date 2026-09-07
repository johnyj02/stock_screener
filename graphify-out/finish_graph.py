import json
from pathlib import Path

out = Path('graphify-out')
ast = json.loads((out / '.graphify_ast.json').read_text())
nodes, edges, hypers = {}, [], []
for item in ast.get('nodes', []):
    nodes[item['id']] = item
edges.extend(ast.get('edges', []))
for path in sorted(out.glob('.graphify_doc_*.json')):
    data = json.loads(path.read_text())
    for item in data.get('nodes', []):
        nodes[item['id']] = item
    edges.extend(data.get('edges', []))
    hypers.extend(data.get('hyperedges', []))
for rel, label in [('.claude/settings.json', 'Claude project settings'), ('.codex/hooks.json', 'Codex project hooks')]:
    path = Path(rel)
    if path.exists():
        ident = rel.replace('/', '_').replace('.', '_')
        nodes.setdefault(ident, {'id': ident, 'label': label, 'file_type': 'document', 'source_file': rel, '_origin': 'config'})
extraction = {'nodes': list(nodes.values()), 'edges': edges, 'hyperedges': hypers, 'input_tokens': 0, 'output_tokens': 0}
(out / '.graphify_extract.json').write_text(json.dumps(extraction, indent=2))
print(f"merged {len(nodes)} nodes, {len(edges)} edges, {len(hypers)} hyperedges")
