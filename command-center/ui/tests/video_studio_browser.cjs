// Actual browser + authenticated local BCC + real FFmpeg fixture. No API mocks.
// Run only against a disposable local instance, with VIDEO_UI_TOKEN_FILE and
// VIDEO_UI_FIXTURE identifying test-owned files. NODE_PATH may locate Playwright.
const {chromium} = require('playwright');
const fs = require('fs');
const assert = require('assert/strict');
(async()=>{
 const base=process.env.VIDEO_UI_URL || 'http://127.0.0.1:8878';
 assert.equal(new URL(base).hostname,'127.0.0.1','This test requires an isolated loopback instance');
 assert(process.env.VIDEO_UI_TOKEN_FILE && process.env.VIDEO_UI_FIXTURE,'Explicit test token/fixture paths required');
 const browser = await chromium.launch({headless:true});
 const context = await browser.newContext({viewport:{width:1720,height:1100}});
 const page = await context.newPage(); const errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 const response = await context.request.post(base+'/api/login',{data:{token:fs.readFileSync(process.env.VIDEO_UI_TOKEN_FILE,'utf8').trim(),label:'video-ui-local-test'}});
 assert.equal(response.status(),200); const auth=await response.json();
 await context.addInitScript(value=>localStorage.setItem('bcc.csrf',value),auth.csrf);
 await page.goto(base+'/#/video-studio');
 await page.getByRole('button',{name:'＋ Новый проект',exact:true}).click();
 await page.getByLabel('Название',{exact:true}).fill('Video Studio UI Proof');
 await page.getByRole('button',{name:'Применить',exact:true}).click();
 await page.locator('.vs-library input[type=file]').setInputFiles(process.env.VIDEO_UI_FIXTURE);
 await page.locator('.vs-media-card').waitFor({timeout:30000});
 await page.locator('.vs-media-card').dblclick();
 await page.locator('.vs-clip').waitFor();
 await page.locator('.vs-clip').first().click();
 await page.getByLabel(/^(Source out, s|Конец исходника, с)$/).fill('1');
 await page.getByLabel(/^(Source out, s|Конец исходника, с)$/).press('Tab');
 await page.waitForTimeout(600);
 await page.getByRole('button',{name:'Цвет',exact:true}).click();
 await page.getByLabel(/^(saturation|Насыщенность)$/).fill('0.5');
 await page.getByLabel(/^(saturation|Насыщенность)$/).press('Tab');
 await page.waitForTimeout(600);
 await page.screenshot({path:'.audit-work/video-studio-color.png',fullPage:true});
 await page.locator('.vs-preview-actions').getByRole('button',{name:'Создать preview',exact:true}).click();
 await page.locator('.vs-preview video').waitFor({timeout:60000});
 await page.locator('.vs-job').getByText('completed',{exact:false}).first().waitFor({timeout:60000});
 await page.getByRole('button',{name:'Экспорт',exact:true}).first().click();
 await page.locator('dialog').getByRole('button',{name:'Применить',exact:true}).click();
 await page.waitForFunction(()=>document.querySelectorAll('.vs-job a[download]').length===2,{timeout:60000});
 await page.screenshot({path:'.audit-work/video-studio-export.png',fullPage:true});
 const id = new URLSearchParams(page.url().split('?')[1]).get('project_id');
 const jobs = await context.request.get(`${base}/api/video-studio/projects/${id}/exports`);
 const data=await jobs.json();assert(data.jobs.every(j=>j.status==='completed')); assert.equal(errors.length,0,errors.join('\n'));
 await page.reload(); await page.locator('.vs-media-card').waitFor(); assert.equal(await page.locator('.vs-job a[download]').count(),2);
 console.log(JSON.stringify({result:'PASS',project_id:id,jobs:data.jobs.map(j=>({id:j.job_id,status:j.status,verification:j.verification})),pageErrors:errors,screenshots:['video-studio-color.png','video-studio-export.png']},null,2));
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
