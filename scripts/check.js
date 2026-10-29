#!/usr/bin/env node
/**
 * Claw 资讯仪表盘 · 发布前自检
 * 用法：node check.js [index.html 路径] [期望日期 YYYY-MM-DD]
 * 退出码：0 = 通过；1 = 有硬性失败（不可发布）
 * 环境无关：本地 Windows 与云端 Linux 均可运行（只需 node）
 */
const fs = require('fs');

const file = process.argv[2] || './work/index.html';
const expect = process.argv[3] || null; // YYYY-MM-DD，缺省用北京时间今天

const shDate = new Date(Date.now() + 8 * 3600 * 1000); // 北京时间
const today = expect || shDate.toISOString().slice(0, 10);
const [ , mm, dd ] = today.split('-').map(Number);
const cnDate = `${mm}月${dd}日`;
const wk = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][shDate.getUTCDay()];

if (!fs.existsSync(file)) {
  console.error(`FAIL: 找不到文件 ${file}`);
  process.exit(1);
}
const s = fs.readFileSync(file, 'utf8');
const fails = [];
const warns = [];
const ok = (label, cond, detail) => {
  console.log(`${cond ? 'OK  ' : 'FAIL'} ${label}${detail ? ' — ' + detail : ''}`);
  if (!cond) fails.push(label);
};

// 1) JS 语法
const m = s.match(/<script>([\s\S]*?)<\/script>/);
if (!m) { fails.push('script 块存在'); console.log('FAIL 未找到 <script> 块'); }
else {
  try { new Function(m[1]); ok('JS 语法', true); }
  catch (e) { ok('JS 语法', false, e.message); }
}

// 2) div 配对
const open = (s.match(/<div\b/g) || []).length;
const close = (s.match(/<\/div>/g) || []).length;
ok('div 标签配对', open === close, `${open}/${close}`);

// 3) pageMeta 与页面 id 一致
const ids = [...s.matchAll(/<div class="page[^"]*" id="(p-[a-z0-9]+)">/g)].map(x => x[1]);
const meta = m ? [...m[1].matchAll(/'(p-[a-z0-9]+)':\s*\{/g)].map(x => x[1]) : [];
const inner = ids.filter(x => x !== 'p-home');
const missing = inner.filter(x => !meta.includes(x));
const extra = meta.filter(x => !inner.includes(x));
ok('pageMeta 与页面 id 一致', missing.length === 0 && extra.length === 0,
  `页面 ${ids.length} 个 / meta ${meta.length} 条${missing.length ? ' 缺:' + missing : ''}${extra.length ? ' 多:' + extra : ''}`);

// 4) 来源链接质量
const srcs = [...s.matchAll(/<a class="src"(?: href="([^"]*)")?/g)];
const noHref = srcs.filter(x => !x[1]).length;
const bad = srcs.filter(x => x[1] && !/^https?:\/\//.test(x[1])).length;
ok('来源链接均为 http(s)', bad === 0, `共 ${srcs.length} 条，无链接 ${noHref} 条，异常 ${bad} 条`);
if (noHref > 0) warns.push(`${noHref} 条卡片无链接（应为「来源见检索记录」类）`);

// 5) 返回按钮只允许出现在 header
const bb = (s.match(/id="backBtn"/g) || []).length;
ok('backBtn 仅 1 处（header）', bb === 1, `实际 ${bb}`);

// 6) 页头日期为今天
const hasToday = s.includes(cnDate);
ok(`页头日期含「${cnDate}」`, hasToday, hasToday ? '' : '⚠️ 日期未更新');
if (hasToday && !s.includes(wk)) warns.push(`页头未出现「${wk}」`);

// 7) 各模块「数据截至」是否还是旧日期
const stale = [...new Set([...s.matchAll(/数据截至\s*(\d{1,2}月\d{1,2}日)/g)].map(x => x[1]))]
  .filter(d => d !== cnDate);
if (stale.length) warns.push(`仍有旧「数据截至」日期：${stale.join('、')}`);
console.log(stale.length ? `WARN 旧「数据截至」日期：${stale.join('、')}` : `OK   「数据截至」日期均为 ${cnDate}`);

// 8) 重复 id
const all = [...s.matchAll(/id="([^"]+)"/g)].map(x => x[1]);
const dup = all.filter((v, i) => all.indexOf(v) !== i);
ok('无重复 id', dup.length === 0, dup.length ? dup.join(',') : '');

console.log('\n' + '-'.repeat(48));
console.log(`结果：${fails.length === 0 ? '✅ 全部通过' : '❌ ' + fails.length + ' 项失败：' + fails.join(' / ')}`);
if (warns.length) console.log('提醒：\n  - ' + warns.join('\n  - '));
console.log(`文件：${file}  (${(fs.statSync(file).size / 1024).toFixed(0)} KB, 校验日期 ${cnDate})`);
process.exit(fails.length === 0 ? 0 : 1);
