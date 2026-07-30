//=============================================================================
// НАИМЕНОВАНИЕ РЕКЛАМОДАТЕЛЯ
//=============================================================================

const ADVERTISER_NAME_MODAL = 'advertiser-name-modal';
let ADVERTISER_NAME_ID = null;
let ADVERTISER_NAME_RETURN_TO_CARD = false;

function initAdvertiserIdentityActions(){
  if(window.__advertiserIdentityActionsInitialized) return;
  window.__advertiserIdentityActionsInitialized = true;
  document.addEventListener('click', e => {
    const el = e.target.closest('[data-action]');
    if(!el) return;
    if(el.dataset.action === 'advertiser-name-close'){
      Signage.closeModal(ADVERTISER_NAME_MODAL);
    }else if(el.dataset.action === 'advertiser-name-save'){
      saveAdvertiserName();
    }
  });
}

function openAdvertiserRename(id, name, returnToCard){
  ADVERTISER_NAME_ID = Number(id);
  ADVERTISER_NAME_RETURN_TO_CARD = Boolean(returnToCard);
  Signage.openModal({
    id: ADVERTISER_NAME_MODAL,
    title: 'Изменить имя рекламодателя',
    body: `
      <div class="fld" style="margin-bottom:0;">
        <label>Наименование</label>
        <input class="inp" id="advertiser-name-value" maxlength="200"
               value="${esc(name || '')}" autocomplete="off">
      </div>
      <div class="muted" style="font-size:11px;margin-top:9px;">
        Новое имя появится в кабинете, медиатеке, кампаниях и новых документах.
      </div>`,
    actions: `
      <button class="btn" data-action="advertiser-name-close">Отмена</button>
      <button class="btn primary" data-action="advertiser-name-save">Сохранить</button>`,
  });
  const input = document.getElementById('advertiser-name-value');
  input?.focus();
  input?.select();
}

async function saveAdvertiserName(){
  const name = val('advertiser-name-value');
  if(!name){
    toast('Введите имя рекламодателя'); return;
  }
  try{
    await api('/advertisers/' + ADVERTISER_NAME_ID, {
      method:'PATCH',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({name}),
    });
    Signage.closeModal(ADVERTISER_NAME_MODAL);
    toast('Имя рекламодателя изменено');
    if(ADVERTISER_NAME_RETURN_TO_CARD){
      Signage.openAdvertiserCard(ADVERTISER_NAME_ID);
    }else{
      Signage.viewAdvertisers();
    }
  }catch(e){
    toast('Ошибка: ' + e.message);
  }
}

window.Signage = window.Signage || {};
window.Signage.openAdvertiserRename = openAdvertiserRename;

initAdvertiserIdentityActions();
