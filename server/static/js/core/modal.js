//=============================================================================
// ЕДИНЫЕ МОДАЛЬНЫЕ ФОРМЫ
//=============================================================================
// Системные prompt/confirm зависят от браузера и плохо тестируются. Рабочие
// формы панели открываются через этот небольшой компонент.

function appOpenModal({id, title, body, actions}){
  appCloseModal(id);
  const modal = document.createElement('div');
  modal.id = id;
  modal.className = 'app-modal';
  modal.innerHTML = `
    <section class="app-modal-card" role="dialog" aria-modal="true"
             aria-labelledby="${id}-title">
      <div class="app-modal-head">
        <div class="app-modal-title" id="${id}-title">${title}</div>
      </div>
      <div class="app-modal-body">${body}</div>
      <div class="app-modal-actions">${actions}</div>
    </section>`;
  document.body.appendChild(modal);
  return modal;
}

function appCloseModal(id){
  document.getElementById(id)?.remove();
}

window.Signage = window.Signage || {};
window.Signage.openModal = appOpenModal;
window.Signage.closeModal = appCloseModal;
