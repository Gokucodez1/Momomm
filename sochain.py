async def get_live_rate(self):
    try:
        res = requests.get(
            f"{self.config['sochain_api']}/get_info/LTC",
            timeout=10
        )
        return float(res.json()['data']['price'])
    except Exception as e:
        print(f"Rate fetch error: {e}")
        return float(self.config['fallback_exchange_rate'])
