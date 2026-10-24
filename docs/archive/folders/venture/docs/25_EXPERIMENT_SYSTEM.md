# Experiment system

An experiment exists to remove one decision-critical uncertainty.

Required:
- hypothesis;
- customer segment;
- evidence before;
- single primary metric;
- secondary metrics;
- max budget;
- max duration;
- sample target;
- success threshold;
- failure threshold;
- stop-loss;
- result;
- decision.

## Good experiment

“Will Prague independent electricians book a 20-minute demo for a job-to-cash
tool at 499 CZK/month?”

Budget 3,000 CZK.
Stop at 1,500 CZK with zero qualified bookings.
Pass if >=6 qualified bookings at <=250 CZK per booking AND >=2 accept a paid
pilot offer.

## Bad experiment

“Run ads and see what happens.”

## Sequential decisions

Do not repeatedly move the goalposts after data arrives.

Changing success criterion creates a new experiment version.
