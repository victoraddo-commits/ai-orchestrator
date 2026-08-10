import { useState, useEffect } from 'react';
import { CreditCard, Check, Zap, Crown, Star, Smartphone, Loader2 } from 'lucide-react';
import { api } from '../lib/api';
import { useAuth } from '../hooks/useAuth';

const PLAN_ICONS = {
  daily: Zap,
  weekly: Star,
  monthly: Crown,
};

export default function Subscribe() {
  const { user } = useAuth();
  const [plans, setPlans] = useState([]);
  const [loading, setLoading] = useState(true);
  const [subscribing, setSubscribing] = useState(null);
  const [phone, setPhone] = useState('');
  const [message, setMessage] = useState(null);

  useEffect(() => {
    api.plans()
      .then((d) => setPlans(Array.isArray(d) ? d : []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const handleSubscribe = async (plan) => {
    setSubscribing(plan.key);
    setMessage(null);
    try {
      const result = await api.purchaseSubscription(user?.id || 1, {
        plan_key: plan.key,
        payment_provider: 'hubtel',
        payment_method: 'mobile_money',
        phone_number: phone,
        currency: 'GHS',
      });
      setMessage({ type: 'success', text: `Subscription activated! Transaction: ${result.transaction_id}` });
    } catch (e) {
      setMessage({ type: 'error', text: e.message });
    } finally {
      setSubscribing(null);
    }
  };

  if (loading) {
    return (
      <div className="max-w-4xl mx-auto space-y-6 animate-pulse">
        <div className="skeleton h-10 w-48" />
        <div className="grid grid-cols-3 gap-6">
          {[...Array(3)].map((_, i) => <div key={i} className="skeleton h-80 rounded-xl" />)}
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-4xl mx-auto space-y-8">
      <div className="text-center">
        <h1 className="text-2xl font-bold text-white flex items-center justify-center gap-2">
          <CreditCard className="w-6 h-6 text-brand-400" />
          Choose Your Plan
        </h1>
        <p className="text-surface-400 text-sm mt-2">Unlock premium predictions and advanced features</p>
      </div>

      {/* Phone input */}
      <div className="max-w-sm mx-auto">
        <label className="text-sm font-medium text-surface-300 block mb-1.5">
          <Smartphone className="w-4 h-4 inline mr-1.5" />
          Mobile Money Number
        </label>
        <input
          type="text"
          className="input"
          placeholder="e.g. 024XXXXXXX"
          value={phone}
          onChange={(e) => setPhone(e.target.value)}
        />
      </div>

      {message && (
        <div className={`max-w-sm mx-auto text-sm p-3 rounded-lg ${
          message.type === 'success'
            ? 'bg-emerald-600/10 text-emerald-400 border border-emerald-600/20'
            : 'bg-red-600/10 text-red-400 border border-red-600/20'
        }`}>
          {message.text}
        </div>
      )}

      {/* Plans */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        {plans.map((plan) => {
          const Icon = PLAN_ICONS[plan.key] || Zap;
          return (
            <div key={plan.key} className={`card flex flex-col ${
              plan.key === 'monthly' ? 'border-brand-600/50 ring-1 ring-brand-600/20' : ''
            }`}>
              {plan.key === 'monthly' && (
                <div className="absolute -top-3 left-1/2 -translate-x-1/2 bg-brand-600 text-white text-xs font-bold px-3 py-0.5 rounded-full">
                  BEST VALUE
                </div>
              )}
              <div className="text-center mb-6">
                <div className="w-12 h-12 rounded-xl bg-brand-600/20 flex items-center justify-center mx-auto mb-3">
                  <Icon className="w-6 h-6 text-brand-400" />
                </div>
                <h3 className="text-lg font-bold text-white">{plan.name}</h3>
                <p className="text-3xl font-bold text-white mt-3">
                  GHS {plan.price?.toFixed(2)}
                  <span className="text-sm font-normal text-surface-400">
                    /{plan.duration_days}d
                  </span>
                </p>
              </div>

              <ul className="space-y-2 mb-6 flex-1">
                <Feature text={`${plan.duration_days} day${plan.duration_days > 1 ? 's' : ''} access`} />
                <Feature text="Premium predictions" />
                <Feature text="All sports coverage" />
                {plan.features?.premium_sports && (
                  <Feature text={`${plan.features.premium_sports.length} premium sports`} />
                )}
                <Feature text={
                  plan.features?.max_picks === -1
                    ? 'Unlimited daily picks'
                    : `${plan.features?.max_picks || 0} daily picks`
                } />
                {plan.key === 'monthly' && <Feature text="Priority Telegram alerts" />}
              </ul>

              <button
                onClick={() => handleSubscribe(plan)}
                disabled={subscribing === plan.key}
                className={`w-full ${plan.key === 'monthly' ? 'btn-primary' : 'btn-secondary'}`}
              >
                {subscribing === plan.key ? (
                  <><Loader2 className="w-4 h-4 animate-spin" /> Processing...</>
                ) : (
                  `Get ${plan.name}`
                )}
              </button>
            </div>
          );
        })}
      </div>

      <div className="card text-center text-sm text-surface-500">
        <p>🔒 Payments processed via <strong className="text-surface-300">Hubtel Mobile Money</strong> (MTN, Telecel, AirtelTigo)</p>
        <p className="mt-1">Currently in test mode — no real charges will be made</p>
      </div>
    </div>
  );
}

function Feature({ text }) {
  return (
    <li className="flex items-center gap-2 text-sm text-surface-300">
      <Check className="w-4 h-4 text-emerald-400 flex-shrink-0" />
      {text}
    </li>
  );
}
