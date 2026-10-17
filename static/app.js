const provider = document.getElementById('provider');
const providerNote = document.getElementById('provider-note');
const notes = {
  gmail: 'Use a Google App Password with 2-Step Verification enabled.',
  outlook: 'App-password IMAP may be rejected by Microsoft. If connection fails, the account may require OAuth2 / Modern Authentication.',
  yahoo: 'Use a Yahoo App Password.',
  icloud: 'Use an Apple app-specific password.'
};
if (provider && providerNote) provider.addEventListener('change', () => providerNote.textContent = notes[provider.value] || '');
const search = document.getElementById('table-search');
const authFilter = document.getElementById('auth-filter');
const table = document.getElementById('results-table');
function filterRows() {
  if (!table) return;
  const query = (search?.value || '').toLowerCase();
  const mode = authFilter?.value || 'all';
  let visible = 0;
  table.querySelectorAll('tbody tr').forEach(row => {
    const matchesText = Array.from(row.cells).slice(0,-1).map(c=>c.innerText).join(' ').toLowerCase().includes(query);
    const auth = row.dataset.auth || '';
    const matchesAuth = mode === 'all' || (mode === 'pass' && auth.includes('pass')) || (mode === 'fail' && auth.includes('fail')) || (mode === 'na' && auth.includes('n/a'));
    row.hidden = !(matchesText && matchesAuth);
    if (!row.hidden) visible++;
  });
  const counter = document.getElementById('visible-count');
  if (counter) counter.textContent = `${visible} visible rows`;
}
search?.addEventListener('input', filterRows);
authFilter?.addEventListener('change', filterRows);
