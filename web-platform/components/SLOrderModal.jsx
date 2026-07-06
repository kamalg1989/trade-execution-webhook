import React, { useState } from 'react';
import { X, Copy, Trash2, AlertCircle, CheckCircle } from 'lucide-react';

export default function SLOrderModal({ position, onClose, onSave }) {
  const [updating, setUpdating] = useState(false);
  const [newSL, setNewSL] = useState(position.stopLoss);
  const [copied, setCopied] = useState(false);

  const slOrders = position.slOrders || [];
  const parentOrderId = position.parentOrderId;

  const getRiskZone = (price, sl) => {
    const distance = ((price - sl) / price) * 100;
    if (distance > 10) return { zone: 'safe', color: 'text-green-400', bgColor: 'bg-green-900' };
    if (distance > 5) return { zone: 'warning', color: 'text-yellow-400', bgColor: 'bg-yellow-900' };
    return { zone: 'critical', color: 'text-red-400', bgColor: 'bg-red-900' };
  };

  const getStatusBadge = (status) => {
    const statusMap = {
      'PENDING': { bg: 'bg-blue-900', text: 'text-blue-200' },
      'ACTIVE': { bg: 'bg-green-900', text: 'text-green-200' },
      'TRIGGERED': { bg: 'bg-yellow-900', text: 'text-yellow-200' },
      'EXECUTED': { bg: 'bg-gray-900', text: 'text-gray-200' },
      'CANCELLED': { bg: 'bg-red-900', text: 'text-red-200' }
    };
    return statusMap[status] || statusMap['PENDING'];
  };

  const handleCopyOrderId = (orderId) => {
    navigator.clipboard.writeText(orderId);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleUpdateSL = async () => {
    if (newSL === position.stopLoss) {
      alert('Stop loss value unchanged');
      return;
    }

    setUpdating(true);
    try {
      await onSave(newSL);
      setUpdating(false);
    } catch (error) {
      console.error('Failed to update SL:', error);
      setUpdating(false);
    }
  };

  const handleCancelSLOrder = async (slOrderId) => {
    if (!window.confirm('Cancel this SL order? Position will remain open without SL protection.')) {
      return;
    }

    try {
      const response = await fetch(`/api/cancel-sl-order/${slOrderId}`, {
        method: 'POST'
      });
      const result = await response.json();
      if (result.success) {
        alert('✅ SL order cancelled');
        onClose();
      } else {
        alert(`❌ Failed: ${result.error}`);
      }
    } catch (error) {
      alert('❌ Failed to cancel SL order');
    }
  };

  const riskZone = getRiskZone(position.currentPrice, position.stopLoss);

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
      <div className="bg-slate-800 rounded-lg max-w-2xl w-full max-h-[90vh] overflow-y-auto">
        {/* Header */}
        <div className="sticky top-0 bg-slate-700 p-6 border-b border-slate-600 flex justify-between items-start">
          <div>
            <h2 className="text-2xl font-bold text-white mb-1">{position.symbol} - Stop Loss Orders</h2>
            <p className="text-slate-300 text-sm">Manage SL orders and risk parameters</p>
          </div>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-white transition-colors"
          >
            <X className="w-6 h-6" />
          </button>
        </div>

        {/* Content */}
        <div className="p-6 space-y-6">
          {/* Position Summary */}
          <div className="bg-slate-700 rounded-lg p-4">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div>
                <p className="text-slate-400 text-sm mb-1">Current Price</p>
                <p className="text-2xl font-bold text-white">₹{position.currentPrice?.toFixed(2)}</p>
              </div>
              <div>
                <p className="text-slate-400 text-sm mb-1">Stop Loss</p>
                <p className="text-2xl font-bold text-red-400">₹{position.stopLoss?.toFixed(2)}</p>
              </div>
              <div>
                <p className="text-slate-400 text-sm mb-1">Distance</p>
                <p className="text-2xl font-bold text-green-400">
                  ₹{(position.currentPrice - position.stopLoss).toFixed(2)}
                </p>
              </div>
              <div>
                <p className="text-slate-400 text-sm mb-1">Risk Zone</p>
                <p className={`text-2xl font-bold ${riskZone.color}`}>
                  {riskZone.zone.toUpperCase()}
                </p>
              </div>
            </div>
          </div>

          {/* Current SL Orders */}
          {slOrders.length > 0 ? (
            <div>
              <h3 className="text-lg font-bold text-white mb-3">Active SL Orders ({slOrders.length})</h3>
              <div className="space-y-3">
                {slOrders.map((order, idx) => {
                  const statusBadge = getStatusBadge(order.status);
                  return (
                    <div key={order.orderId || idx} className="bg-slate-700 rounded-lg p-4 border border-slate-600">
                      <div className="flex justify-between items-start mb-3">
                        <div>
                          <p className="font-bold text-white mb-1">{order.orderType || 'Stop Loss'} Order</p>
                          <p className="text-xs text-slate-400 font-mono break-all">
                            ID: {order.orderId}
                            <button
                              onClick={() => handleCopyOrderId(order.orderId)}
                              className="ml-2 text-blue-400 hover:text-blue-300"
                            >
                              <Copy className="w-3 h-3 inline" />
                            </button>
                            {copied && <span className="ml-2 text-green-400 text-xs">Copied!</span>}
                          </p>
                        </div>
                        <span className={`px-3 py-1 rounded-full text-sm font-semibold ${statusBadge.bg} ${statusBadge.text}`}>
                          {order.status}
                        </span>
                      </div>

                      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3 text-sm">
                        <div>
                          <p className="text-slate-400">SL Price</p>
                          <p className="font-bold text-red-400">₹{order.triggerPrice?.toFixed(2)}</p>
                        </div>
                        <div>
                          <p className="text-slate-400">Quantity</p>
                          <p className="font-bold text-white">{order.quantity}</p>
                        </div>
                        <div>
                          <p className="text-slate-400">Validity</p>
                          <p className="font-bold text-white">{order.validity || 'DAY'}</p>
                        </div>
                        <div>
                          <p className="text-slate-400">Created</p>
                          <p className="font-bold text-white text-xs">
                            {new Date(order.createdAt).toLocaleDateString()}
                          </p>
                        </div>
                      </div>

                      {/* Order Details */}
                      {order.executedPrice && (
                        <div className="bg-slate-600 rounded p-2 mb-3 text-sm">
                          <p className="text-slate-300">
                            <strong>Executed at:</strong> ₹{order.executedPrice?.toFixed(2)}
                          </p>
                        </div>
                      )}

                      {/* Actions */}
                      {order.status === 'ACTIVE' || order.status === 'PENDING' ? (
                        <button
                          onClick={() => handleCancelSLOrder(order.orderId)}
                          className="bg-red-600 hover:bg-red-700 text-white px-3 py-2 rounded text-sm flex items-center gap-2 transition-all"
                        >
                          <Trash2 className="w-4 h-4" />
                          Cancel Order
                        </button>
                      ) : (
                        <div className="flex items-center gap-2 text-slate-400 text-sm">
                          <CheckCircle className="w-4 h-4" />
                          {order.status === 'EXECUTED' ? 'Position closed at SL' : 'Order cancelled'}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          ) : (
            <div className="bg-yellow-900 border border-yellow-700 rounded-lg p-4 flex items-start gap-3">
              <AlertCircle className="w-5 h-5 text-yellow-300 flex-shrink-0 mt-0.5" />
              <div>
                <p className="font-semibold text-yellow-200">No Active SL Orders</p>
                <p className="text-sm text-yellow-300 mt-1">
                  This position does not have an active SL order placed. Update the stop loss below to create one.
                </p>
              </div>
            </div>
          )}

          {/* Update SL Form */}
          <div className="bg-slate-700 rounded-lg p-4 border border-blue-600 border-opacity-30">
            <h3 className="text-lg font-bold text-white mb-4">Update Stop Loss</h3>
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-slate-300 text-sm font-semibold mb-2">Current SL</label>
                  <input
                    type="number"
                    value={position.stopLoss}
                    disabled
                    className="w-full bg-slate-600 text-slate-300 px-3 py-2 rounded opacity-50 cursor-not-allowed"
                  />
                </div>
                <div>
                  <label className="block text-slate-300 text-sm font-semibold mb-2">New SL</label>
                  <input
                    type="number"
                    step="0.01"
                    value={newSL}
                    onChange={(e) => setNewSL(parseFloat(e.target.value))}
                    className="w-full bg-slate-600 text-white px-3 py-2 rounded focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                </div>
              </div>

              {newSL !== position.stopLoss && (
                <div className="bg-slate-600 rounded p-3 text-sm">
                  <p className="text-slate-300 mb-2">
                    <strong>Change:</strong> ₹{(newSL - position.stopLoss).toFixed(2)}
                    <span className={newSL > position.stopLoss ? ' text-green-400' : ' text-red-400'}>
                      {newSL > position.stopLoss ? ' (↑ Increase)' : ' (↓ Decrease)'}
                    </span>
                  </p>
                  <p className="text-slate-300">
                    <strong>New Distance:</strong> ₹{(position.currentPrice - newSL).toFixed(2)}
                    ({(((position.currentPrice - newSL) / position.currentPrice) * 100).toFixed(2)}%)
                  </p>
                </div>
              )}

              <div className="flex gap-3 pt-2">
                <button
                  onClick={handleUpdateSL}
                  disabled={updating || newSL === position.stopLoss}
                  className="flex-1 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed text-white font-semibold py-2 px-4 rounded transition-all"
                >
                  {updating ? 'Updating...' : 'Update Stop Loss'}
                </button>
                <button
                  onClick={onClose}
                  className="flex-1 bg-slate-600 hover:bg-slate-500 text-white font-semibold py-2 px-4 rounded transition-all"
                >
                  Close
                </button>
              </div>
            </div>
          </div>

          {/* Tips */}
          <div className="bg-slate-700 rounded-lg p-4 text-sm text-slate-300 space-y-2">
            <p className="font-semibold text-white mb-2">💡 Tips:</p>
            <ul className="list-disc list-inside space-y-1">
              <li>Always keep SL at least 2-3% below entry price to avoid false exits</li>
              <li>Adjust SL as price moves up to lock in profits (trailing stop)</li>
              <li>In critical zone, consider reducing position size instead of lowering SL</li>
              <li>SL orders are automatically cancelled when position is closed</li>
            </ul>
          </div>
        </div>
      </div>
    </div>
  );
}
