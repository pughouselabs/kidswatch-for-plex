// Real Chromium checks against the disposable HTTPS fixture. No real accounts.
const assert = require('node:assert/strict');
const {chromium} = require(process.env.KIDSWATCH_PLAYWRIGHT_MODULE);
(async () => {
  const browser = await chromium.launch({executablePath: process.env.UX_CHROMIUM || '/usr/bin/chromium',headless: true,args:['--no-sandbox']});
  try {
    const context = await browser.newContext({ignoreHTTPSErrors: true,viewport:{width:390,height:844}});
    // Only this synthetic browser fixture trusts the ephemeral test certificate.
    const page = await context.newPage();
    const errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    const base=process.env.KIDSWATCH_FIXTURE_ORIGIN;
    await page.goto(base+'/dev/login?u=alex');
    await page.getByRole('button',{name:'Create code',exact:true}).click();
    await page.locator('.phone-code').waitFor();
    assert.equal(await page.locator('svg[aria-labelledby="qr-caption"]').count(),1);
    assert.equal(await page.locator('[data-phone-pairing]').evaluate(e=>e.scrollWidth<=e.clientWidth),true);
    assert.equal(await page.evaluate(()=>Object.keys(sessionStorage).some(k=>/pair|token|phone/.test(k))),false);
    await page.getByRole('button',{name:'Cancel code',exact:true}).click();
    await page.getByText('Connection code canceled.',{exact:true}).waitFor();
    assert.equal(await page.locator('.phone-code').count(),0);
    await page.getByRole('button',{name:'Create new code',exact:true}).click();
    await page.locator('.phone-code').waitFor();
    const code=(await page.locator('.phone-code').innerText()).replaceAll(' ','');
    const instance=(await context.request.get(base+'/api/mobile/v1/meta',{headers:{Accept:'application/json','X-KidsWatch-Client':'android/1'}})).json();
    const meta=await instance;
    const redeemed=await context.request.post(base+'/api/mobile/v1/pairings/redeem',{headers:{Accept:'application/json','X-KidsWatch-Client':'android/1'},data:{code,instance_id:meta.data.instance_id,device_label:'Browser test phone'}});
    assert.equal(redeemed.status(),201);
    await page.getByRole('link',{name:'Connected phones',exact:true}).click();
    await page.getByRole('heading',{name:'Browser test phone',exact:true}).waitFor();
    page.once('dialog',dialog=>dialog.accept());
    await page.getByRole('button',{name:'Revoke access for Browser test phone'}).click();
    await page.getByText('Access revoked. This phone needs a new connection code.',{exact:true}).waitFor();
    assert.deepEqual(errors,[]);
    console.log('Chromium mobile pairing/create/cancel/manual redemption/device revoke: PASS (390px, no runtime errors)');
    await context.close();
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exit(1);});
