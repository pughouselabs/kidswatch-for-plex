// Start a fresh `python -m tests.dev_server` before each width.
// Development tools: playwright-core and axe-core on NODE_PATH.
// WIDTH=390 node tests/browser_onboarding.cjs (repeat with WIDTH=1280).
const {chromium} = require('playwright-core');
const fs = require('fs');
const assert = require('assert/strict');
const base = 'http://127.0.0.1:8791';
const out = process.env.BROWSER_ARTIFACT_DIR || require('path').join(require('os').tmpdir(), 'kidswatch-onboarding');
fs.mkdirSync(out, {recursive:true});
const axe = fs.readFileSync(require.resolve('axe-core/axe.min.js'), 'utf8');
(async () => {
 const browser = await chromium.launch({executablePath:process.env.CHROMIUM_PATH || '/usr/bin/chromium',headless:true});
 const results = [];
 for (const width of [Number(process.env.WIDTH || 390)]) {
  const context = await browser.newContext({viewport:{width,height:900}});
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', e=>errors.push(e.message));
  page.on('console', m=>{if(m.type()==='error') errors.push(m.text());});
  async function verify(name, subject=page) {
   await subject.evaluate(()=>document.fonts.ready);
   assert(await subject.evaluate(()=>document.documentElement.scrollWidth <= innerWidth),`overflow: ${width} ${name}`);
   await subject.evaluate(axe);
   const audit = await subject.evaluate(async()=>await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa','wcag22aa']}}));
   const violations = audit.violations.map(v=>({id:v.id,impact:v.impact,nodes:v.nodes.map(n=>n.target)}));
   await subject.screenshot({path:`${out}/${width}-${name}.png`,fullPage:true});
   results.push({width,name,url:subject.url(),violations});
   assert.deepEqual(violations,[],`accessibility: ${width} ${name}`);
  }
  await page.goto(base+'/dev-login?u=owner&next=/admin');
  await page.getByRole('link',{name:'Owner setup',exact:true}).click();
  await verify('setup-start');
  await page.getByRole('link',{name:'Continue setup',exact:true}).click();
  await verify('setup-libraries');
  await page.locator('input[name=sections][value="1"]').check();
  await page.locator('input[name=sections][value="2"]').check();
  await page.getByRole('button',{name:'Continue',exact:true}).click();
  await verify('setup-notifications');
  await page.getByRole('button',{name:'Send test notification',exact:true}).click();
  await page.getByText('Test sent. Check your device.',{exact:true}).waitFor();
  await page.locator('input[name=confirmed]').check();
  await page.locator('input[name=trusted]').check();
  await page.getByRole('button',{name:'Finish setup',exact:true}).click();
  await verify('setup-ready');
  await page.getByRole('link',{name:'Open Admin',exact:true}).click();
  await page.getByRole('link',{name:'Invite parents',exact:true}).click();
  await verify('invite-new');
  await page.locator('#parent-id').selectOption('103');
  await page.getByRole('button',{name:'Create invite',exact:true}).click();
  await verify('invite-ready');
  await page.getByRole('button',{name:'Copy link',exact:true}).click();
  await page.getByText('Invitation link copied.',{exact:true}).waitFor();
  const invite = await page.locator('#invite-link').inputValue();
  const parentContext = await browser.newContext({viewport:{width,height:900}});
  const parent = await parentContext.newPage();
  await parent.goto(invite);
  await verify('invite-preview',parent);
  // Plex itself is simulated, while the app handles its real PIN and callback.
  await parentContext.request.post(base+'/dev-plex-account?u=casey');
  await parent.getByRole('link',{name:'Sign in with Plex',exact:true}).click();
  await parent.waitForURL(url => url.pathname === new URL(invite).pathname);
  await verify('invite-signed-in',parent);
  await parent.getByRole('button',{name:'Request access',exact:true}).click();
  await parent.waitForURL(base+'/waiting');
  await verify('parent-waiting',parent);
  await page.getByRole('link',{name:'Done',exact:true}).click();
  await page.getByRole('link',{name:'Parent access requests',exact:true}).click();
  await verify('approval-inbox');
  await page.getByRole('link',{name:'casey',exact:true}).first().click();
  await verify('approval-detail');
  await page.getByRole('button',{name:'Approve parent',exact:true}).click();
  await verify('approval-complete');
  await parent.getByRole('link',{name:'Check access',exact:true}).click();
  await parent.waitForURL(base+'/home');
  await parentContext.close();
  await page.goto(base+'/dev-login?u=alex&next=/kids/2');
  await verify('child-connect');
  assert.equal(await page.locator('#email-tip-address').inputValue(),'jane+kid2plex@gmail.com');
  const makeLink = page.getByRole('button',{name:'Get sign-in link',exact:true});
  if(await makeLink.count()) await makeLink.click();
  await page.getByRole('button',{name:'Copy link',exact:true}).click();
  await verify('child-link');
  const childLink=await page.locator('#link-box').inputValue();
  const childContext=await browser.newContext({viewport:{width,height:900}});
  const child=await childContext.newPage();
  await child.goto(childLink);
  await verify('child-private-link',child);
  // A unique fixture account is needed: Kid1 already belongs to the sample child.
  await childContext.request.post(base+'/dev-plex-account?u=stranger');
  await child.getByRole('link',{name:'Sign in as Kid2',exact:true}).click();
  await child.getByText(/All set\. Kid2's titles show up/).waitFor();
  await verify('child-connected',child);
  await child.screenshot({path:`${out}/${width}-child-connected.png`,fullPage:true});
  await childContext.close();
  await page.reload();
  assert.equal(await page.locator('#email-tip-address').count(),0);
  assert.deepEqual(errors,[]);
  await context.close();
 }
 fs.writeFileSync(`${out}/${process.env.WIDTH || 390}-results.json`,JSON.stringify(results,null,2));
 await browser.close();
 console.log(JSON.stringify({checks:results.length,violations:results.flatMap(r=>r.violations).length,output:out}));
})().catch(e=>{console.error(e);process.exit(1)});
