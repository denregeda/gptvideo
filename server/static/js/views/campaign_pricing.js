//=============================================================================
// ФИНАНСОВЫЕ УСЛОВИЯ КАМПАНИИ
//=============================================================================

const CAMPAIGN_PRICING_MODAL = 'campaign-pricing-modal';
let CAMPAIGN_PRICING_ID = null;

function initCampaignPricingActions(){
  if(window.__campaignPricingActionsInitialized) return;
  window.__campaignPricingActionsInitialized = true;
  document.addEventListener('click', e => {
    const el = e.target.closest('[data-action]');
    if(!el) return;
    if(el.dataset.action === 'campaign-pricing-close'){
      Signage.closeModal(CAMPAIGN_PRICING_MODAL);
    }else if(el.dataset.action === 'campaign-pricing-save'){
      saveCampaignPricing();
    }
  });
}

async function openCampaignPricing(campaignId){
  try{
    const c = await api('/campaigns/' + campaignId);
    const f = c.financial || {};
    CAMPAIGN_PRICING_ID = campaignId;
    Signage.openModal({
      id: CAMPAIGN_PRICING_MODAL,
      title: `Условия кампании «${esc(c.name)}»`,
      body: `
        <div class="row">
          <div class="fld" style="flex:1;">
            <label>Тариф</label>
            <select class="inp" id="campaign-pricing-mode">
              <option value="per_play" ${f.billing_mode==='per_play'?'selected':''}>За показ</option>
              <option value="per_minute" ${f.billing_mode==='per_minute'?'selected':''}>За минуту</option>
            </select>
          </div>
          <div class="fld" style="flex:1;">
            <label>Цена единицы, ₽</label>
            <input class="inp" id="campaign-pricing-unit" type="number" min="0"
                   step="0.01" value="${Number(f.unit_price || 0).toFixed(2)}">
          </div>
        </div>
        <div class="fld">
          <label>Ручная скидка, ₽</label>
          <input class="inp" id="campaign-pricing-discount" type="number" min="0"
                 step="0.01" value="${Number(f.discount_amount || 0).toFixed(2)}">
        </div>
        <div class="fld">
          <label>Основание скидки</label>
          <input class="inp" id="campaign-pricing-note"
                 value="${esc(c.discount_note || '')}"
                 placeholder="Обязательно, если скидка больше нуля">
        </div>
        <div class="muted" style="font-size:11px;">
          Изменение фиксируется в журнале. Условия других кампаний не меняются.
        </div>`,
      actions: `
        <button class="btn" data-action="campaign-pricing-close">Отмена</button>
        <button class="btn primary" data-action="campaign-pricing-save">Сохранить</button>`,
    });
    document.getElementById('campaign-pricing-unit')?.focus();
  }catch(e){
    toast('Ошибка: ' + e.message);
  }
}

async function saveCampaignPricing(){
  const mode = val('campaign-pricing-mode');
  const price = Number(val('campaign-pricing-unit').replace(',', '.'));
  const discount = Number(val('campaign-pricing-discount').replace(',', '.'));
  const note = val('campaign-pricing-note');
  if(!['per_play', 'per_minute'].includes(mode)){
    toast('Выберите тариф'); return;
  }
  if(!Number.isFinite(price) || price < 0){
    toast('Цена должна быть неотрицательным числом'); return;
  }
  if(!Number.isFinite(discount) || discount < 0){
    toast('Скидка должна быть неотрицательным числом'); return;
  }
  if(discount > 0 && !note){
    toast('Для ручной скидки укажите основание'); return;
  }
  try{
    await api('/campaigns/' + CAMPAIGN_PRICING_ID, {
      method:'PATCH',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        billing_mode:mode,
        unit_price:price,
        discount_amount:discount,
        discount_note:discount > 0 ? note : '',
      }),
    });
    Signage.closeModal(CAMPAIGN_PRICING_MODAL);
    toast('Финансовые условия обновлены и записаны в журнал');
    Signage.viewCampaigns();
  }catch(e){
    toast('Ошибка: ' + e.message);
  }
}

window.Signage = window.Signage || {};
window.Signage.openCampaignPricing = openCampaignPricing;

initCampaignPricingActions();
