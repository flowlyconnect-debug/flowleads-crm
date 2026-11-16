const {chromium}=require('playwright');
(async()=>{const b=await chromium.launch();const p=await b.newPage({viewport:{width:1280,height:720}});
await p.goto('file://'+process.argv[2]);await p.evaluate(()=>document.fonts.ready);
await p.pdf({path:process.argv[3],width:'1280px',height:'720px',printBackground:true,preferCSSPageSize:true});
await b.close();})();
