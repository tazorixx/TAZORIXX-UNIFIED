import test from 'node:test';
import assert from 'node:assert/strict';
import {normalizeRecipient,normalizeRecipients,isTemporarySendError,retryDelayMs} from '../src/modules/whatsapp/broadcast-utils.ts';

test('India local mobile numbers gain country code',()=>{
 assert.equal(normalizeRecipient('9876543210'),'919876543210@s.whatsapp.net');
 assert.equal(normalizeRecipient('+91 98765 43210'),'919876543210@s.whatsapp.net');
});
test('preserves international and group JIDs',()=>{
 assert.equal(normalizeRecipient('628123456789@s.whatsapp.net'),'628123456789@s.whatsapp.net');
 assert.equal(normalizeRecipient('12345-123@g.us'),'12345-123@g.us');
});
test('deduplicates and rejects malformed recipients',()=>{
 assert.deepEqual(normalizeRecipients(['9876543210','+91 9876543210']),['919876543210@s.whatsapp.net']);
 assert.throws(()=>normalizeRecipient('abc@s.whatsapp.net'));
 assert.throws(()=>normalizeRecipients([]));
});
test('retry classification and bounded backoff',()=>{
 assert.equal(isTemporarySendError(new Error('Connection Closed')),true);
 assert.equal(isTemporarySendError(new Error('Invalid JID')),false);
 assert.ok(retryDelayMs(10)<=15000);
});
