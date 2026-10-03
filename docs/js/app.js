/* 전국 상권 분석 대시보드
 *
 * 데이터는 data/dashboard.json 하나를 fetch 해서 쓴다.
 * 원본 277만 점을 브라우저로 옮기지 않고, 파이프라인이 사전 집계해 둔 결과만 쓴다.
 */
'use strict';

const DATA_URL = 'data/dashboard.json';

const CATS = [
  '카페', '편의점', '치킨', '미용실', '피부 관리실', '부동산 중개/대리업',
  '입시·교과학원', '빵/도넛', '노래방', '약국', '세탁소', '안경',
];

let D = null;          // 대시보드 JSON
let map = null;        // Leaflet 지도
let gridLayer = null;  // 격자 레이어
let lastCenter = null; // 마지막으로 선택한 중심점

const $ = (s) => document.querySelector(s);
const fmt = (n) => (typeof n === 'number' ? n.toLocaleString('ko-KR') : n ?? '-');
const escapeHtml = (s) => String(s).replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

/* ------------------------------------------------------------------ 부트 */
document.addEventListener('DOMContentLoaded', async () => {
  try {
    const res = await fetch(DATA_URL, { cache: 'no-cache' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    D = await res.json();
    if (D.error) throw new Error(D.error);
  } catch (err) {
    $('#loading').innerHTML =
      `<div style="text-align:center;padding:20px">
         <p style="font-size:15px;color:#16191d;margin:0 0 8px">데이터를 불러오지 못했습니다.</p>
         <p style="font-size:12.5px;max-width:460px;margin:0 auto">${escapeHtml(err.message)}</p>
         <p style="font-size:12px;color:#6b7280;margin-top:14px">
           예시 데이터로 화면을 보려면
           <code>site/data/sample.json</code> 을 <code>dashboard.json</code> 으로 복사하세요.
         </p>
       </div>`;
    return;
  }
  $('#loading').classList.add('hidden');

  renderMeta();
  initTabs();
  initCompare();
  initWhite();
  initTrend();
  initMap();
});

/* ------------------------------------------------------------------ 메타 */
function renderMeta() {
  const m = D.meta;
  const ym = D.pop_meta && D.pop_meta[0] ? D.pop_meta[0].기준연월 : '미반영';
  $('#meta').innerHTML = [
    `기준 분기 <b>${m.version}</b>`,
    `업소 <b>${fmt(m.total)}</b>개`,
    `갱신 <b>${m.updated}</b>`,
    `인구 기준 <b>${ym}</b>`,
  ].map((s) => `<span>${s}</span>`).join('');
}

/* ------------------------------------------------------------------ 탭 */
function initTabs() {
  document.querySelectorAll('.tab').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach((b) => b.classList.remove('active'));
      document.querySelectorAll('.panel').forEach((p) => p.classList.remove('active'));
      btn.classList.add('active');
      $('#tab-' + btn.dataset.tab).classList.add('active');
      // 지도는 처음 열 때만 그려야 로드 성능이 안 무너진다
      if (btn.dataset.tab === 'map' && !map) initMap();
    });
  });
}

/* ------------------------------------------------------------------ 지도 */
function initMap() {
  if (map) return;
  map = L.map('map', { center: [36.5, 127.8], zoom: 7, scrollWheelZoom: false });
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 18,
    attribution: '© OpenStreetMap 기여자',
  }).addTo(map);

  $('#radius').addEventListener('change', () => { if (lastCenter) showRadius(lastCenter); });
  $('#gridMetric').addEventListener('change', drawGrid);

  map.on('click', (e) => showRadius(e.latlng));
  drawGrid();
}

function drawGrid() {
  if (!map) return;
  const metric = $('#gridMetric').value;
  const vals = D.grid.map((c) => c[metric] || 0);
  const max = Math.max(...vals, 1);

  const layer = L.layerGroup();
  for (const c of D.grid) {
    const v = c[metric] || 0;
    if (v <= 0) continue;
    const t = Math.min(v / max, 1);
    const rect = L.rectangle(
      [[c.lat, c.lon], [c.lat + 0.02, c.lon + 0.02]],
      { color: '#2f6fed', weight: 0.3, opacity: 0.35,
        fillColor: '#2f6fed', fillOpacity: 0.08 + t * 0.55 }
    );
    rect.bindTooltip(`${metric} ${fmt(v)}개`, { sticky: true });
    layer.addLayer(rect);
  }
  if (gridLayer) map.removeLayer(gridLayer);
  gridLayer = layer.addTo(map);
}

/** 클릭한 지점 주변 grid 셀들을 모아 반경 내 업소를 추정한다. */
function showRadius(latlng) {
  const rDeg = parseFloat($('#radius').value);
  const cells = [];
  for (const c of D.grid) {
    // 격자 중심까지 거리
    const dy = (c.lat + 0.01) - latlng.lat;
    const dx = (c.lon + 0.01) - latlng.lng;
    const km = Math.hypot(dy * 111, dx * 88);
    if (km <= (rDeg === 0.005 ? 0.9 : rDeg === 0.01 ? 1.6 : 2.5)) cells.push(c);
  }

  const sum = {};
  let total = 0;
  for (const c of cells) {
    total += c.n;
    for (const k of CATS) sum[k] = (sum[k] || 0) + (c[k] || 0);
  }

  const label = $('#radius').selectedOptions[0].textContent;
  $('#radTitle').textContent = `반경 ${label} 내 경쟁 현황`;
  $('#radWhere').textContent =
    `${latlng.lat.toFixed(4)}, ${latlng.lng.toFixed(4)} · 포함 격자 ${cells.length}개`;

  const maxV = Math.max(...CATS.map((k) => sum[k] || 0), 1);
  const rows = CATS.map((k) => ({ k, v: sum[k] || 0 })).sort((a, b) => b.v - a.v);

  $('#radBody').className = '';
  $('#radBody').innerHTML = `
    <div class="rad-summary">
      <div class="k"><span>전체 업소</span><b>${fmt(total)}</b></div>
      ${CATS.slice(0, 4).map((k) => `
        <div class="k"><span>${k}</span><b>${fmt(sum[k] || 0)}</b></div>`).join('')}
    </div>
    <table><thead><tr><th>업종</th><th>점포수</th><th>구성</th></tr></thead><tbody>
      ${rows.map((r) => `
        <tr>
          <td>${r.k}</td>
          <td class="num">${fmt(r.v)}</td>
          <td class="bar-cell">
            <div class="fill" style="width:${(r.v / maxV) * 100}%"></div>
            <span>${total ? ((r.v / total) * 100).toFixed(1) : 0}%</span>
          </td>
        </tr>`).join('')}
    </tbody></table>`;

  if (lastCenter && lastCenter.circle) map.removeLayer(lastCenter.circle);
  const circle = L.circle(latlng, {
    radius: rDeg * 111000,
    color: '#e11d48', weight: 2, fillOpacity: 0.06,
  }).addTo(map).bindTooltip(`반경 ${label}`);
  lastCenter = { circle, latlng };
}

/* ------------------------------------------------------------------ 지역 비교 */
const CMP_METRIC_LABEL = {
  n: '업소 수', per1k: '인구 1,000명당', cafe10k: '카페 1만명당',
  cvs10k: '편의점 1만명당', hair10k: '미용실 1만명당', hagwon10k: '입시학원 1만명당',
  realty10k: '부동산 1만명당', food: '음식 비중', sci: '과학·기술 비중',
  edu: '교육 비중', lodging: '숙박 비중',
};

function initCompare() {
  ['cmpLevel', 'cmpMetric', 'cmpTop'].forEach((id) =>
    $('#' + id).addEventListener('change', renderCompare));
  $('#cmpSearch').addEventListener('input', renderCompare);
  renderCompare();

  chartBar('#cSido', shortName(D.sido.map((r) => r.sido)), D.sido.map((r) => r.n), '#2f6fed');
  chartPie('#cMajor', D.major.map((r) => r.k), D.major.map((r) => r.n));
  chartBar('#cSmall', D.small.slice(0, 20).map((r) => r.k),
    D.small.slice(0, 20).map((r) => r.n), '#3cb371', true);

  chartBar('#cSeoul', D.seoul_gu.map((r) => r.gu),
    D.seoul_gu.map((r) => r.n), '#8e6ccf', true);
}

function renderCompare() {
  const level = $('#cmpLevel').value;
  const metric = $('#cmpMetric').value;
  const top = parseInt($('#cmpTop').value, 10);
  const q = $('#cmpSearch').value.trim();

  // 인구 결합이 없으면 인구당 지표는 계산할 수 없다
  const densityOnly = metric !== 'n' && !['food', 'sci', 'edu', 'lodging'].includes(metric);
  if (densityOnly && !D.sgg_density) {
    $('#cmpTable').innerHTML =
      `<tr><td class="empty">이 지표는 주민등록 인구 데이터가 필요합니다. ` +
      `<code>scripts/refresh.py</code> 로 다시 생성하세요.</td></tr>`;
    return;
  }

  let rows;
  if (metric === 'n' || ['food', 'sci', 'edu', 'lodging'].includes(metric)) {
    rows = level === 'sgg' ? D.sgg : D.sido;
  } else {
    rows = level === 'sgg' ? D.sgg_density : D.sido_density;
  }
  rows = rows.filter((r) => r[metric] !== undefined && r[metric] !== null);
  rows = [...rows].sort((a, b) => b[metric] - a[metric]);

  if (q) {
    rows = rows.filter((r) =>
      String(r.sgg || '').includes(q) || String(r.sido || '').includes(q));
  }
  if (top) rows = rows.slice(0, top);

  const label = CMP_METRIC_LABEL[metric];
  const hasPop = rows.some((r) => r.pop !== undefined);
  const maxV = rows.length ? rows[0][metric] : 1;
  // metric 이 'n' 이면 '업소' 칸이 곧 지표라 두 번 그리지 않는다
  const showShopCol = metric !== 'n';

  $('#cmpHint').textContent =
    `${level === 'sgg' ? '시군구' : '시도'} 기준 · ${label}` +
    (rows.length ? ` · 표시 ${rows.length}건` : ' · 결과 없음');

  $('#cmpTable').innerHTML = `
    <thead><tr>
      <th>시도</th><th>시군구</th>
      ${showShopCol ? '<th class="num">업소</th>' : ''}
      ${hasPop ? '<th class="num">인구</th>' : ''}
      <th class="num">${label}</th><th>비교</th>
    </tr></thead>
    <tbody>
      ${rows.map((r) => `
        <tr>
          <td>${r.sido ?? '-'}</td>
          <td>${r.sgg ?? r.sido ?? '-'}</td>
          ${showShopCol ? `<td class="num">${fmt(r.n)}</td>` : ''}
          ${hasPop ? `<td class="num">${fmt(r.pop)}</td>` : ''}
          <td class="num">${fmt(r[metric])}</td>
          <td class="bar-cell">
            <div class="fill" style="width:${maxV ? (r[metric] / maxV) * 100 : 0}%"></div>
            <span></span>
          </td>
        </tr>`).join('')}
    </tbody>`;
}

/* ------------------------------------------------------------------ 미집약 업종 */
function initWhite() {
  const sel = $('#whiteCat');
  sel.innerHTML = CATS.map((c) => `<option>${c}</option>`).join('');
  ['whiteCat', 'whiteMinPop', 'whiteMinShops'].forEach((id) =>
    $('#' + id).addEventListener('change', renderWhite));
  renderWhite();
}

function renderWhite() {
  const cat = $('#whiteCat').value;
  const minPop = parseInt($('#whiteMinPop').value, 10);
  const minShops = parseInt($('#whiteMinShops').value, 10);

  if (!D.sgg_density) {
    $('#whiteTable').innerHTML =
      `<tr><td class="empty">주민등록 인구 데이터가 없어 1만명당 점포수를 계산할 수 없습니다.</td></tr>`;
    return;
  }
  if (!D.sgg_focus) {
    $('#whiteTable').innerHTML =
      `<tr><td class="empty">업종별 시군구 집계 데이터가 없습니다.</td></tr>`;
    return;
  }

  const byName = new Map(D.sgg_focus.map((r) => [`${r.sido}|${r.sgg}`, r]));
  let rows = D.sgg_density
    .filter((r) => r.pop >= minPop && r.n >= minShops)
    .map((r) => {
      const f = byName.get(`${r.sido}|${r.sgg}`);
      const n = f && f[cat] !== undefined ? f[cat] : 0;
      return { ...r, catN: n, per10k: Math.round((10000 * n) / r.pop * 10) / 10 };
    })
    .sort((a, b) => a.per10k - b.per10k);

  rows = rows.slice(0, 40);
  const maxN = rows.length ? Math.max(...rows.map((r) => r.catN)) : 1;

  $('#whiteTable').innerHTML = `
    <thead><tr>
      <th>시도</th><th>시군구</th><th class="num">${cat}</th>
      <th class="num">1만명당</th><th class="num">인구</th>
      <th class="num">전체 업소</th><th>점포 규모</th>
    </tr></thead>
    <tbody>
      ${rows.map((r) => `
        <tr>
          <td>${r.sido}</td>
          <td>${r.sgg}</td>
          <td class="num">${fmt(r.catN)}</td>
          <td class="num">${r.per10k}</td>
          <td class="num">${fmt(r.pop)}</td>
          <td class="num">${fmt(r.n)}</td>
          <td class="bar-cell">
            <div class="fill" style="width:${maxN ? (r.catN / maxN) * 100 : 0}%"></div>
            <span></span>
          </td>
        </tr>`).join('')}
    </tbody>`;
}

/* ------------------------------------------------------------------ 추이 */
function initTrend() {
  const h = D.history || [];
  if (h.length < 2) {
    $('#trendHint').innerHTML =
      `지금까지 ${h.length}개 분기만 쌓였습니다. 분기 데이터는 1년 4번 나오므로 ` +
      `추이를 보려면 몇 분기가 더 지나야 합니다. (${h.map((x) => x.version).join(', ')})`;
    $('#trendTable').innerHTML =
      `<tr><td class="empty">추이를 그릴 데이터가 아직 부족합니다.</td></tr>`;
  } else {
    $('#trendHint').textContent = `${h.length}개 분기 누적`;
    const majors = Object.keys(h[0].major);
    $('#trendTable').innerHTML = `
      <thead><tr><th>분기</th><th class="num">전체 업소</th>
        <th class="num">전 대비 증감</th>
        ${majors.map((m) => `<th class="num">${m}</th>`).join('')}
      </tr></thead>
      <tbody>
        ${h.map((r, i) => {
          const prev = i > 0 ? h[i - 1].total : null;
          const diff = prev === null ? '-' : `${r.total - prev >= 0 ? '+' : ''}${fmt(r.total - prev)}`;
          return `<tr>
            <td>${r.version}</td>
            <td class="num">${fmt(r.total)}</td>
            <td class="num">${diff}</td>
            ${majors.map((m) => `<td class="num">${fmt(r.major[m] || 0)}</td>`).join('')}
          </tr>`;
        }).join('')}
      </tbody>`;
  }

  const labels = h.map((r) => r.version);
  chartLine('#cTrend', labels, [{ label: '전체 업소', data: h.map((r) => r.total), borderColor: '#2f6fed' }]);

  const majors = Object.keys(h[0]?.major || {});
  chartMix('#cTrendMix', labels, majors.map((m, i) => ({
    label: m,
    data: h.map((r) => (r.total ? (r.major[m] || 0) / r.total * 100 : 0)),
    borderColor: PALETTE[i % PALETTE.length],
  })));
}

/* ------------------------------------------------------------------ chart helpers */
const PALETTE = ['#2f6fed', '#f2a53a', '#3cb371', '#e11d48', '#8e6ccf',
                 '#20b2c7', '#c98a3f', '#7f8c8d', '#d4a5e0', '#5c7cfa'];

function shortName(list) {
  const rep = (s) => String(s)
    .replace('전남광주통합특별시', '전남광주')
    .replace('특별자치도', '').replace('통합특별시', '')
    .replace('특별자치시', '').replace('광역시', '').replace('특별시', '')
    .replace('경상남도', '경남').replace('경상북도', '경북')
    .replace('충청남도', '충남').replace('충청북도', '충북');
  return [...new Set(list.map(rep))];
}

function chartBar(sel, labels, data, color, horizontal) {
  new Chart($(sel), {
    type: 'bar',
    data: { labels, datasets: [{ data, backgroundColor: color, borderRadius: 3 }] },
    options: {
      indexAxis: horizontal ? 'y' : 'x',
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { font: { size: 10.5 } }, grid: { display: !horizontal } },
        y: { ticks: { font: { size: 10.5 } }, grid: { display: horizontal } },
      },
    },
  });
}

function chartPie(sel, labels, data) {
  new Chart($(sel), {
    type: 'doughnut',
    data: { labels, datasets: [{ data, backgroundColor: PALETTE }] },
    options: { plugins: { legend: { position: 'right', labels: { font: { size: 11 } } } } },
  });
}

function chartLine(sel, labels, series) {
  new Chart($(sel), {
    type: 'line',
    data: {
      labels,
      datasets: series.map((s) => ({
        ...s, borderWidth: 2, tension: 0.25,
        pointRadius: 3, fill: false,
      })),
    },
    options: {
      plugins: { legend: { labels: { font: { size: 11 } } } },
      scales: { y: { ticks: { font: { size: 10.5 } } } },
    },
  });
}

function chartMix(sel, labels, series) {
  new Chart($(sel), {
    type: 'line',
    data: {
      labels,
      datasets: series.map((s) => ({ ...s, borderWidth: 2, tension: 0.25, pointRadius: 3 })),
    },
    options: {
      plugins: {
        legend: { labels: { font: { size: 10.5 }, boxWidth: 10 } },
        tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${c.parsed.y.toFixed(1)}%` } },
      },
      scales: { y: { ticks: { font: { size: 10.5 }, callback: (v) => v + '%' } } },
    },
  });
}