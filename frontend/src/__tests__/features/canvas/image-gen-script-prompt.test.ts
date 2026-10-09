import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import ts from 'typescript';
import { expect, it } from 'vitest';

it('script-derived images submit their own anchored shot without the entire screenplay', () => {
  const source = readFileSync(resolve(process.cwd(), 'src/features/canvas/nodes/ImageGenNode.tsx'), 'utf8');
  const file = ts.createSourceFile('ImageGenNode.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const expressions = new Map<string, string>();
  const visit = (node: ts.Node) => {
    if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && node.initializer) {
      if (['isScriptStoryboardNode', 'effectivePrompt'].includes(node.name.text)) {
        expressions.set(node.name.text, node.initializer.getText(file));
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  expect(expressions.size).toBe(2);
  const run = new Function('data', 'ownPrompt', 'upstreamTextJoined', 'shouldInlineUpstreamTextAsPrompt', 'hasUserEditedPromptRef',
    `const isScriptStoryboardNode = ${expressions.get('isScriptStoryboardNode')}; return ${expressions.get('effectivePrompt')};`);
  const own = '资产图锚定：\n角色甲的参考图是 图片1\n\n甲的近景';
  for (const data of [{ scriptRowKey: 'shot:6' }, { scriptShotId: 'shot_6' }]) {
    expect(run(data, own, '整份剧本'.repeat(20000), false, { current: false })).toBe(own);
    expect(run(data, '', '整份剧本', false, { current: false })).toBe('');
  }
  expect(run({}, own, '导演总纲', false, { current: false })).toBe(`导演总纲\n\n${own}`);
  expect(run({}, '', '场景文本', true, { current: false })).toBe('场景文本');
  expect(run({}, '', '场景文本', true, { current: true })).toBe('');
});
