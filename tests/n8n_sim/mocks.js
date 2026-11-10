/*
 * Fake Oikotie / Finder / Asiakastieto / OpenAI for the n8n catalogue e2e test.
 *
 * Uusimaa has 30 listings (page 1: 24, page 2: 6); every other maakunta is empty.
 *   1001: lock renovation coming + isännöitsijä contacts on the page
 *   1002: lock renovation coming, no contacts  -> Contact Enricher finds them
 *   1003: pipe renovation already done          -> no renovations
 *   1004: detail page fails (HTTP 500)          -> analysis failed, retried later
 *   others: no renovation info
 */
const UUSIMAA_IDS = Array.from({ length: 30 }, (_, i) => 1001 + i);

function card(id) {
  return {
    cardId: id,
    url: `https://asunnot.oikotie.fi/myytavat-asunnot/espoo/${id}`,
    location: { city: 'Espoo', address: `Testikatu ${id}` },
    data: { price: '250 000 €', size: '62 m²', description: `Valoisa koti ${id}` },
  };
}

const DETAIL = {
  1001: 'Taloyhtiö As Oy Lukkotesti. Tulevat remontit: Lukituksen uusiminen (iLOQ) suunnitteilla 2027. ' +
        'Isännöitsijä: Isännöinti Testi Oy, puh 040 123 4567, testi@isannointi.fi',
  1002: 'Taloyhtiö As Oy Avaimeton. Tulevat remontit: Kulunvalvonta ja lukituksen uusiminen iLOQ 2026.',
  1003: 'Taloyhtiö As Oy Putki. Tehdyt remontit: Putkiremontti tehty 2015.',
};

function page(text) {
  const filler = ' Asunnon kuvaus: kodikas kaksio hyvällä paikalla, lähellä palveluita ja julkisia yhteyksiä. '.repeat(4);
  return `<html><body><h1>Kohde</h1><div>${text}${filler}</div><script>var x=1;</script></body></html>`;
}

exports.http = ({ url }) => {
  const u = new URL(url);
  if (u.hostname === 'asunnot.oikotie.fi') {
    if (u.pathname === '/api/search') {
      const offset = Number(u.searchParams.get('offset') || 0);
      const locations = u.searchParams.get('locations') || '';
      const ids = locations.includes('uusimaa') || locations.includes('Uusimaa') ? UUSIMAA_IDS : [];
      return { status: 200, text: JSON.stringify({ cards: ids.slice(offset, offset + 24).map(card) }) };
    }
    if (u.pathname.startsWith('/myytavat-asunnot/espoo/')) {
      const id = Number(u.pathname.split('/').pop());
      if (id === 1004) return { status: 500, text: 'oops' };
      return { status: 200, text: page(DETAIL[id] || `Kohde ${id}. Ei tietoja tulevista remonteista.`) };
    }
    if (u.pathname.startsWith('/myytavat-asunnot')) {
      const slug = u.pathname.split('/')[2] || '';
      return {
        status: 200,
        headers: { 'set-cookie': ['session=abc; Path=/', 'other=1; Path=/'] },
        text: `<html><head><meta name="api-token" content="tok"><meta name="cuid" content="cu"><meta name="loaded" content="1"></head>` +
              `<body>"locations":[{"id":1,"type":4,"name":"${slug}"}] Myytävät asunnot: 30 kpl</body></html>`,
      };
    }
  }
  if (u.hostname === 'www.finder.fi') {
    if (u.pathname === '/search') {
      return { status: 200, text: '<a href="/Isännöinti/Avaimeton+Isännöinti+Oy/Espoo/9999">Avaimeton Isännöinti Oy</a>' };
    }
    return { status: 200, text: 'Avaimeton Isännöinti Oy Puhelin 040 999 8888 www https://avaimeton-isannointi.fi' };
  }
  if (u.hostname === 'www.asiakastieto.fi') {
    if (u.pathname.startsWith('/yritykset/haku')) {
      return { status: 200, text: '<a href="/yritykset/fi/avaimeton-isannointi-oy/12345678/yleiskuva">x</a>' };
    }
    return { status: 200, text: 'Puhelin 040 999 8888' };
  }
  return { status: 404, text: 'not mocked: ' + url };
};

exports.openai = (values, nodeName) => {
  const system = values.filter(v => v.role === 'system').map(v => v.content).join('\n');
  const text = values.filter(v => v.role !== 'system').map(v => v.content).join('\n');
  if (nodeName === 'AI – Analyze Listing') {
    if (!system.includes('- Lukkoremontti / kulunvalvonta')) throw new Error('analysis prompt is missing the CRM types');
    const renovations = [];
    if (text.includes('iLOQ')) {
      renovations.push({ remonttityyppi: 'Lukkoremontti / kulunvalvonta', confidence: 88, timing: '2027', evidence: 'Lukituksen uusiminen (iLOQ)' });
    }
    const out = {
      taloyhtio: (text.match(/Taloyhtiö (As Oy \w+)/) || [])[1] || '',
      isannoitsija: text.includes('Isännöinti Testi Oy') ? 'Isännöinti Testi Oy' : '',
      isannoitsija_puh: text.includes('040 123 4567') ? '040 123 4567' : '',
      isannoitsija_email: text.includes('testi@isannointi.fi') ? 'testi@isannointi.fi' : '',
      tulevat_remontit: renovations.length ? 'Lukitus uusitaan' : '',
      renovations,
    };
    return '```json\n' + JSON.stringify(out) + '\n```';
  }
  if (nodeName === 'AI – Extract Fallback Contact Info') {
    return JSON.stringify({
      fallback_isannoitsija: '',
      fallback_isannointiyhtio: 'Avaimeton Isännöinti Oy',
      fallback_in_puh: '040 999 8888',
      fallback_in_email: '',
      fallback_source: 'finder',
      fallback_confidence: 70,
    });
  }
  throw new Error('unexpected AI node ' + nodeName);
};
