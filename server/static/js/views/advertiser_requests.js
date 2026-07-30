//=============================================================================
// КАБИНЕТ РЕКЛАМОДАТЕЛЯ: заявки на размещение
//=============================================================================

async function advCreateRequest(){
  const body = {
    date_from: val('adv-rq-from'),
    date_to: val('adv-rq-to'),
    plays_wanted: Number(val('adv-rq-plays') || 0),
    comment: val('adv-rq-comment'),
  };
  if(!body.date_from || !body.date_to || body.date_from > body.date_to){
    toast('Проверьте период заявки'); return;
  }
  if(body.plays_wanted <= 0){
    toast('Укажите желаемое количество выходов'); return;
  }
  try{
    await api(`/advertisers/${ADV_STATE.id}/requests`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify(body)
    });
    toast('Заявка отправлена');
    advRender();
  }catch(e){ toast('Ошибка: ' + e.message); }
}

async function advRequests(){
  const rows = await api(`/advertisers/${ADV_STATE.id}/requests`);
  const status = {
    new:'На рассмотрении', approved:'Одобрена', declined:'Отклонена',
    campaign:'Создана кампания'
  };
  let h = `<div class="sec" style="margin-top:0;">Новая заявка на размещение</div>
    <div class="cell">
      <div class="muted" style="font-size:12px;margin-bottom:8px;">
        Укажите период и желаемое число выходов. Тариф, индивидуальную цену
        и возможную скидку согласует администратор при создании кампании.
      </div>
      <div class="row">
        <div class="fld" style="flex:1;"><label>С</label>
          <input class="inp" type="date" id="adv-rq-from" value="${ADV_STATE.from}"></div>
        <div class="fld" style="flex:1;"><label>По</label>
          <input class="inp" type="date" id="adv-rq-to" value="${ADV_STATE.to}"></div>
        <div class="fld" style="flex:1;"><label>Желаемое число выходов</label>
          <input class="inp" type="number" min="1" id="adv-rq-plays"></div>
      </div>
      <div class="fld"><label>Комментарий</label>
        <input class="inp" id="adv-rq-comment" placeholder="Пожелания по размещению"></div>
      <button class="btn primary" data-action="adv-request-create">Отправить заявку</button>
    </div>`;

  h += '<div class="sec">История заявок</div>';
  if(!rows.length) return h + '<div class="empty">Заявок пока нет</div>';
  h += '<div class="cell" style="overflow-x:auto;"><table style="font-size:12px;">'
    + '<tr><th>Создана</th><th>Период</th><th style="text-align:right;">Выходов</th>'
    + '<th>Статус</th><th style="text-align:left;">Комментарий</th></tr>';
  rows.forEach(r => {
    h += `<tr><td class="muted">${esc(fmtServerTS(r.created_at))}</td>
      <td>${r.period_start} — ${r.period_end}</td>
      <td style="text-align:right;">${r.plays_wanted || '—'}</td>
      <td style="text-align:center;">${status[r.status] || esc(r.status)}</td>
      <td class="muted">${esc(r.decision_note || r.comment || '—')}</td></tr>`;
  });
  return h + '</table></div>';
}
