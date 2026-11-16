// Rakentaa FlowLeads-käyttöohjeen PDF:n: node docs/ohjeet/build.js
// Kansi renderöidään reunoihin asti, sisältö A4-marginaaleilla ja sivunumeroilla.
const {chromium} = require('playwright');
const {execFileSync} = require('child_process');
const path = require('path');
const dir = __dirname;
const out = path.join(dir, 'FlowLeads-kayttoohje.pdf');

(async () => {
  const b = await chromium.launch();
  const p = await b.newPage();
  await p.goto('file://' + path.join(dir, 'kansi.html'));
  await p.evaluate(() => document.fonts.ready);
  await p.pdf({path: path.join(dir, '_kansi.pdf'), format: 'A4', printBackground: true, preferCSSPageSize: true});
  await p.goto('file://' + path.join(dir, 'ohje.html'));
  await p.evaluate(() => document.fonts.ready);
  await p.pdf({
    path: path.join(dir, '_sisalto.pdf'), format: 'A4', printBackground: true, preferCSSPageSize: true,
    displayHeaderFooter: true, headerTemplate: '<div></div>',
    footerTemplate: '<div style="width:100%;margin:0 18mm;padding-top:6px;border-top:1px solid #dcd8e4;display:flex;justify-content:space-between;font-family:Inter,sans-serif;font-size:7.5pt;color:#515368"><span>FlowLeads-käyttöohje · flowlysolutions.net</span><span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>',
  });
  await b.close();
  execFileSync('python3', ['-c', `
import pypdf,sys
w=pypdf.PdfWriter()
for f in sys.argv[1:3]: w.append(f)
w.add_metadata({'/Title':'FlowLeads-käyttöohje','/Author':'Flowly Solutions'})
w.write(sys.argv[3])
import os
for f in sys.argv[1:3]: os.remove(f)
`, path.join(dir, '_kansi.pdf'), path.join(dir, '_sisalto.pdf'), out]);
  console.log(out);
})();
