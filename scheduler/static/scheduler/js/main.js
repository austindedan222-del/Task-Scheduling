/*
 * Progressive-enhancement UI layer.
 *
 * Every action here (add/edit/delete task) already works as a plain
 * server-rendered page/redirect without this file — main.js only
 * layers modals, inline validation, and toast feedback on top via
 * fetch() + the "X-Requested-With: XMLHttpRequest" header, which the
 * Django views check for before deciding to return JSON instead of a
 * full page. No client-side framework, no new backend behaviour.
 */
(function () {
    'use strict';

    /* ---------- Mobile nav toggle ---------- */
    document.addEventListener('DOMContentLoaded', function () {
        var toggle = document.getElementById('navToggle');
        var nav = document.getElementById('mainNav');
        if (toggle && nav) {
            toggle.addEventListener('click', function () {
                var isOpen = nav.classList.toggle('open');
                toggle.setAttribute('aria-expanded', isOpen);
            });
        }
        promoteServerMessagesToToasts();
    });

    /* ---------- Toasts ---------- */
    function showToast(message, type) {
        var root = document.getElementById('toastRoot');
        if (!root || !message) return;
        var toast = document.createElement('div');
        toast.className = 'toast toast-' + (type || 'info');
        toast.textContent = message;
        root.appendChild(toast);
        requestAnimationFrame(function () { toast.classList.add('toast-visible'); });
        setTimeout(function () { dismissToast(toast); }, 4500);
        toast.addEventListener('click', function () { dismissToast(toast); });
    }

    function dismissToast(toast) {
        toast.classList.remove('toast-visible');
        setTimeout(function () { toast.remove(); }, 250);
    }

    function promoteServerMessagesToToasts() {
        var list = document.getElementById('serverMessages');
        if (!list) return;
        list.querySelectorAll('.message').forEach(function (li) {
            var type = 'info';
            if (li.classList.contains('message-success')) type = 'success';
            if (li.classList.contains('message-error')) type = 'error';
            if (li.classList.contains('message-warning')) type = 'warning';
            showToast(li.textContent.trim(), type);
        });
        list.remove();
    }

    /* ---------- Modal ---------- */
    var overlay = document.getElementById('modalOverlay');
    var modalTitle = document.getElementById('modalTitle');
    var modalBody = document.getElementById('modalBody');
    var modalClose = document.getElementById('modalClose');

    function openModal(title, html) {
        if (!overlay) return;
        modalTitle.textContent = title || '';
        modalBody.innerHTML = html || '';
        overlay.hidden = false;
        document.body.classList.add('modal-open');
        requestAnimationFrame(function () { overlay.classList.add('modal-visible'); });
        var firstField = modalBody.querySelector('input, textarea, select, button');
        if (firstField) firstField.focus();

        // Sync the priority badge immediately in case this modal's form
        // has a priority value already filled in (e.g. editing a task).
        var priorityField = modalBody.querySelector('[name="priority"]');
        if (priorityField) updatePriorityBadge(priorityField);
    }

    function closeModal() {
        if (!overlay) return;
        overlay.classList.remove('modal-visible');
        document.body.classList.remove('modal-open');
        setTimeout(function () {
            overlay.hidden = true;
            modalBody.innerHTML = '';
        }, 150);
    }

    if (modalClose) modalClose.addEventListener('click', closeModal);
    if (overlay) overlay.addEventListener('click', function (e) {
        if (e.target === overlay) closeModal();
    });
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && overlay && !overlay.hidden) closeModal();
    });

    /* ---------- Fetch helper ---------- */
    function fetchJson(url, options) {
        options = options || {};
        options.headers = Object.assign({ 'X-Requested-With': 'XMLHttpRequest' }, options.headers || {});
        return fetch(url, options).then(function (response) {
            return response.json().then(function (data) {
                return { ok: response.ok, data: data };
            });
        });
    }

    /* ---------- Open modal for add / edit / delete ---------- */
    document.addEventListener('click', function (e) {
        var addBtn = e.target.closest('.js-add-task');
        var editBtn = e.target.closest('.js-edit-task');
        var deleteBtn = e.target.closest('.js-delete-task');
        var cancelBtn = e.target.closest('.js-cancel-task');

        if (addBtn) {
            fetchJson(addBtn.dataset.url).then(function (res) {
                openModal(res.data.title || 'Add Task', res.data.html);
            });
        } else if (editBtn) {
            fetchJson(editBtn.dataset.url).then(function (res) {
                openModal(res.data.title || 'Edit Task', res.data.html);
            });
        } else if (deleteBtn) {
            fetchJson(deleteBtn.dataset.url).then(function (res) {
                openModal(res.data.title || ('Delete "' + deleteBtn.dataset.title + '"?'), res.data.html);
            });
        } else if (cancelBtn) {
            if (modalBody && modalBody.contains(cancelBtn)) {
                e.preventDefault();
                closeModal();
            }
            // Outside a modal (standalone page) this is a normal link —
            // let the browser navigate to My Tasks as usual.
        }
    });

    /* ---------- Live priority badge ---------- */
    // Listens on input/change/keyup together so this is reliable across
    // typing, the number spinner arrows, and browser autofill — some of
    // those only fire a subset of these events depending on the browser.
    ['input', 'change', 'keyup'].forEach(function (eventName) {
        document.addEventListener(eventName, function (e) {
            if (e.target.matches('[name="priority"]')) {
                updatePriorityBadge(e.target);
            }
        });
    });

    document.addEventListener('input', function (e) {
        if (e.target.matches('.js-task-form [name], .js-task-form textarea')) {
            clearFieldError(e.target);
        }
    });

    function updatePriorityBadge(input) {
        var badge = document.getElementById('priorityLiveBadge');
        if (!badge) return;
        var value = parseInt(input.value, 10);
        badge.textContent = isNaN(value) ? '-' : value;
        badge.className = 'priority-live-badge';
        if (!isNaN(value)) {
            if (value >= 8) badge.classList.add('priority-high');
            else if (value >= 5) badge.classList.add('priority-mid');
            else if (value >= 1) badge.classList.add('priority-low');
        }
    }

    /* ---------- Client-side validation ---------- */
    function validateTaskForm(form) {
        var valid = true;

        var title = form.querySelector('[name="title"]');
        if (title && !title.value.trim()) {
            setFieldError(form, 'title', 'Title is required.');
            valid = false;
        }

        var deadline = form.querySelector('[name="deadline"]');
        if (deadline && !deadline.value) {
            setFieldError(form, 'deadline', 'Deadline is required.');
            valid = false;
        }

        var priority = form.querySelector('[name="priority"]');
        if (priority) {
            var pVal = parseInt(priority.value, 10);
            if (isNaN(pVal) || pVal < 1 || pVal > 10 || String(pVal) !== priority.value.trim()) {
                setFieldError(form, 'priority', 'Priority must be a whole number from 1 to 10.');
                valid = false;
            }
        }

        var duration = form.querySelector('[name="duration_value"]');
        if (duration) {
            var dVal = parseInt(duration.value, 10);
            if (isNaN(dVal) || dVal <= 0) {
                setFieldError(form, 'duration_value', 'Duration must be a positive number.');
                valid = false;
            }
        }

        var percent = form.querySelector('[name="percent_complete"]');
        if (percent && percent.value !== '') {
            var pcVal = parseInt(percent.value, 10);
            if (isNaN(pcVal) || pcVal < 0 || pcVal > 100) {
                setFieldError(form, 'percent_complete', 'Percent complete must be from 0 to 100.');
                valid = false;
            }
        }

        return valid;
    }

    function setFieldError(form, name, message) {
        var hint = form.querySelector('.js-field-hint[data-for="' + name + '"]');
        if (hint) {
            hint.textContent = message;
            hint.classList.add('field-hint-error');
        }
        var input = form.querySelector('[name="' + name + '"]');
        if (input) input.classList.add('input-invalid');
    }

    function clearFieldError(input) {
        var form = input.closest('form');
        if (!form) return;
        var name = input.getAttribute('name');
        var hint = form.querySelector('.js-field-hint[data-for="' + name + '"]');
        if (hint) {
            hint.textContent = '';
            hint.classList.remove('field-hint-error');
        }
        input.classList.remove('input-invalid');
    }

    function setButtonLoading(form, loading) {
        var btn = form.querySelector('.js-submit-btn');
        if (!btn) return;
        btn.disabled = loading;
        btn.classList.toggle('btn-loading', loading);
    }

    /* ---------- Task create/edit form submit (modal or standalone page) ---------- */
    document.addEventListener('submit', function (e) {
        var form = e.target.closest('.js-task-form');
        if (form) {
            e.preventDefault();
            if (!validateTaskForm(form)) {
                showToast('Please fix the highlighted fields.', 'error');
                return;
            }
            setButtonLoading(form, true);
            fetchJson(form.action, { method: 'POST', body: new FormData(form) })
                .then(function (res) { handleTaskFormResponse(form, res.ok, res.data); })
                .catch(function () {
                    setButtonLoading(form, false);
                    showToast('Something went wrong — please try again.', 'error');
                });
            return;
        }

        var deleteForm = e.target.closest('.js-delete-form');
        if (deleteForm) {
            e.preventDefault();
            setButtonLoading(deleteForm, true);
            fetchJson(deleteForm.action, { method: 'POST', body: new FormData(deleteForm) })
                .then(function (res) { handleDeleteFormResponse(deleteForm, res.ok, res.data); })
                .catch(function () {
                    setButtonLoading(deleteForm, false);
                    showToast('Something went wrong — please try again.', 'error');
                });
        }
    });

    function handleTaskFormResponse(form, ok, data) {
        var inModal = modalBody && modalBody.contains(form);

        if (ok && data.success) {
            showToast(data.message, 'success');
            if (inModal) closeModal();
            refreshTaskTable(data.table_html, inModal);
        } else {
            var container = inModal ? modalBody : form.parentElement;
            container.innerHTML = data.html;
            showToast('Please fix the errors below.', 'error');
        }
    }

    function handleDeleteFormResponse(form, ok, data) {
        var inModal = modalBody && modalBody.contains(form);

        if (ok && data.success) {
            showToast(data.message, 'success');
            if (inModal) closeModal();
            refreshTaskTable(data.table_html, inModal);
        } else {
            setButtonLoading(form, false);
            showToast('Could not delete that task.', 'error');
        }
    }

    function refreshTaskTable(tableHtml, cameFromModal) {
        var container = document.getElementById('taskTableContainer');
        if (container && tableHtml) {
            container.innerHTML = tableHtml;
            return;
        }
        if (cameFromModal) {
            // Modal was opened from a page with no live table (e.g. the
            // dashboard) — reload so counts/cards stay accurate.
            window.location.reload();
            return;
        }
        // Standalone add/edit/delete page with nowhere to inject the
        // table (e.g. a direct link to /tasks/add/) — go to My Tasks.
        window.location.href = (window.APP_URLS && window.APP_URLS.taskList) || '/tasks/';
    }
})();
