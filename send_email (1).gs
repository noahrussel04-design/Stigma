// STIGMA email sender. Paste this into a new Google Apps Script project.
// 1. Change CHANGE_ME below to a long secret you make up.
// 2. Run it once (Run > doPost is fine, it will ask permission for Gmail), then approve.
// 3. Deploy > New deployment > type Web app > Execute as: Me > Who has access: Anyone > Deploy.
// 4. Copy the web app URL. Put it in Render as EMAIL_WEBHOOK_URL, and your secret as EMAIL_WEBHOOK_TOKEN.
const TOKEN = 'CHANGE_ME';

function doPost(e) {
  try {
    const d = JSON.parse(e.postData.contents);
    if (d.token !== TOKEN) return ContentService.createTextOutput('denied');
    GmailApp.sendEmail(d.to, d.subject || '(no subject)', d.body || '');
    return ContentService.createTextOutput('ok');
  } catch (err) {
    return ContentService.createTextOutput('error');
  }
}
