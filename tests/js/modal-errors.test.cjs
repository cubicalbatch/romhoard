const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const root = path.resolve(__dirname, '../..');

function loadApp(errorMessage) {
    const events = new Map();
    const elements = new Map();
    const stores = new Map();
    const element = () => ({
        dataset: {}, children: [], style: {},
        classList: { add() {}, remove() {} },
        append(...children) { this.children.push(...children); },
        replaceChildren(...children) { this.children = children; },
        set innerHTML(value) {
            assert.ok(!value.includes(errorMessage), 'Untrusted error was inserted as HTML');
        },
    });
    const context = {
        document: {
            cookie: '', body: { style: {} }, documentElement: { dataset: {} },
            createElement: element,
            addEventListener(name, callback) { events.set(name, callback); },
            dispatchEvent() {},
            getElementById(id) {
                if (!elements.has(id)) elements.set(id, element());
                return elements.get(id);
            },
        },
        localStorage: { getItem() { return null; } },
        matchMedia() { return { matches: false, addEventListener() {} }; },
        addEventListener() {},
        CustomEvent: class {},
        console, setTimeout, clearTimeout,
        fetch: async () => { throw new Error(errorMessage); },
        Alpine: { store(name, value) {
            if (value !== undefined) stores.set(name, value);
            return stores.get(name);
        } },
    };
    context.window = context;
    vm.createContext(context);
    for (const file of ['romhoard-utils.js', 'alpine-app.js']) {
        vm.runInContext(fs.readFileSync(path.join(root, 'static/js', file), 'utf8'), context);
    }
    events.get('alpine:init')();
    return { stores, elements };
}

for (const action of ['download', 'send', 'romset']) {
    test(`${action} renders untrusted errors as text and keeps the close action`, async () => {
        const message = '<img src=x onerror="alert(1)"> & "bad file"';
        const { stores, elements } = loadApp(message);
        const manager = stores.get('downloadManager');
        manager.deviceId = 1;
        if (action === 'download') await manager.startDownload('/download/', {});
        if (action === 'send') await manager.startSend('/send/', {});
        if (action === 'romset') await stores.get('romsetPicker').start('csrf');
        const container = elements.get(action === 'send' ? 'send-progress-content' : 'download-modal-content');
        const [text, close] = container.children[0].children;
        assert.equal(text.textContent, `Error: ${message}`);
        assert.equal(close.dataset.modalClose, action === 'send' ? 'send-progress' : 'download-progress');
        assert.equal(close.textContent, 'Close');
    });
}
