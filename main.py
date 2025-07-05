import discord
import json
import random
import asyncio
from discord.ext import commands
from sochain import SoChainHandler
from urllib.parse import quote

# Initialize
intents = discord.Intents.all()
bot = commands.Bot(command_prefix='!', intents=intents)
monitor = SoChainHandler()

# Load config
with open('config.json') as f:
    config = json.load(f)

# Data management
def load_data():
    try:
        with open('data/active_deals.json') as f:
            return json.load(f)
    except:
        return {}

def save_data(data):
    with open('data/active_deals.json', 'w') as f:
        json.dump(data, f)

active_deals = load_data()

# Helper functions
def generate_id():
    return ''.join(random.choices('ABCDEFGHJKLMNPQRSTUVWXYZ23456789', k=32))

async def add_user_to_channel(channel, user_id):
    try:
        user = await bot.fetch_user(int(user_id))
        member = channel.guild.get_member(user.id)
        await channel.set_permissions(member, read_messages=True, send_messages=True)
        return member
    except:
        return None

# Step 1: Channel creation
@bot.event
async def on_guild_channel_create(channel):
    if channel.category_id == int(config['category_id']):
        tx_id = generate_id()
        unique_num = random.randint(100, 999)
        
        active_deals[tx_id] = {
            'channel_id': channel.id,
            'stage': 'awaiting_dev_id',
            'unique_num': unique_num
        }
        save_data(active_deals)
        
        await channel.send(f"`{tx_id}`\n`{unique_num}`")
        await channel.send("Please send the Developer ID of the user you're dealing with.\nsend `cancel` to cancel the deal")

# Step 2: Developer ID handling
@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    
    tx_id = next((k for k,v in active_deals.items() if v['channel_id'] == message.channel.id and v['stage'] == 'awaiting_dev_id'), None)
    
    if tx_id:
        if message.content.lower() == 'cancel':
            await message.channel.send("Deal cancelled.")
            del active_deals[tx_id]
            save_data(active_deals)
            return
        
        member = await add_user_to_channel(message.channel, message.content)
        if member:
            active_deals[tx_id]['participants'] = [message.author.id, member.id]
            active_deals[tx_id]['stage'] = 'role_selection'
            save_data(active_deals)
            
            await message.channel.send(f"Added {member.mention} to the ticket!")
            await handle_role_selection(message.channel)
        else:
            await message.channel.send("Invalid Developer ID. Please try again.")
    
    await bot.process_commands(message)

# Step 3: Role selection
async def handle_role_selection(channel):
    tx_id = next((k for k,v in active_deals.items() if v['channel_id'] == channel.id), None)
    if not tx_id: return
    
    class RoleView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=None)
        
        @discord.ui.button(label="Sending Litecoin (Buyer)", style=discord.ButtonStyle.green)
        async def buyer(self, interaction, button):
            if interaction.user.id not in active_deals[tx_id]['participants']:
                return await interaction.response.send_message("You're not part of this deal", ephemeral=True)
            active_deals[tx_id]['buyer'] = interaction.user.id
            save_data(active_deals)
            await interaction.response.edit_message(content=f"{interaction.user.mention} selected as Buyer", view=None)
            await handle_amount_confirmation(channel)
        
        @discord.ui.button(label="Receiving Litecoin (Seller)", style=discord.ButtonStyle.red)
        async def seller(self, interaction, button):
            if interaction.user.id not in active_deals[tx_id]['participants']:
                return await interaction.response.send_message("You're not part of this deal", ephemeral=True)
            active_deals[tx_id]['seller'] = interaction.user.id
            save_data(active_deals)
            await interaction.response.edit_message(content=f"{interaction.user.mention} selected as Seller", view=None)
            await handle_amount_confirmation(channel)
    
    await channel.send("**Select your role:**", view=RoleView())

# Step 4: Amount confirmation
async def handle_amount_confirmation(channel):
    tx_id = next((k for k,v in active_deals.items() if v['channel_id'] == channel.id), None)
    if not tx_id: return
    
    await channel.send("Please enter the deal amount in USD (numbers only):")
    
    def check(m):
        return m.author.id in active_deals[tx_id]['participants'] and m.channel == channel
    
    try:
        msg = await bot.wait_for('message', check=check, timeout=300)
        amount = float(msg.content)
        active_deals[tx_id]['amount_usd'] = amount
        save_data(active_deals)
        
        await handle_payment_instructions(channel, amount)
    except asyncio.TimeoutError:
        await channel.send("Amount entry timed out.")

# Step 5: Payment instructions
async def handle_payment_instructions(channel, amount_usd):
    tx_id = next((k for k,v in active_deals.items() if v['channel_id'] == channel.id), None)
    if not tx_id: return
    
    rate = await monitor.get_live_rate()
    ltc_amount = amount_usd / rate
    address = monitor.address
    
    # Generate QR code URL
    qr_url = config['qr_code_url'].format(
        address=address,
        amount=ltc_amount
    )
    
    embed = discord.Embed(
        title="Payment Instructions",
        description=f"Send exactly `{ltc_amount:.8f} LTC` to:",
        color=0x00FF00
    )
    embed.add_field(name="Address", value=f"`{address}`", inline=False)
    embed.add_field(name="USD Value", value=f"`${amount_usd:.2f}`", inline=True)
    embed.add_field(name="Exchange Rate", value=f"`1 LTC = ${rate:.2f} USD`", inline=True)
    
    class PaymentView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=None)
        
        @discord.ui.button(label="Show QR Code", style=discord.ButtonStyle.blurple)
        async def qr_code(self, interaction, button):
            await interaction.response.send_message(f"Scan to pay: {qr_url}", ephemeral=True)
        
        @discord.ui.button(label="Paste Address", style=discord.ButtonStyle.grey)
        async def paste_address(self, interaction, button):
            await interaction.response.send_message(
                f"```\n{address}\n{ltc_amount:.8f}\n```",
                ephemeral=True
            )
    
    await channel.send(embed=embed, view=PaymentView())
    await handle_transaction_monitoring(channel, amount_usd)

# Step 6: Transaction monitoring
async def handle_transaction_monitoring(channel, amount_usd):
    tx_id = next((k for k,v in active_deals.items() if v['channel_id'] == channel.id), None)
    if not tx_id: return
    
    tx_data = await monitor.monitor_transaction(amount_usd)
    if not tx_data:
        await channel.send("Payment not detected within time limit.")
        return
    
    # Transaction detected embed
    embed = discord.Embed(
        title="Transaction Detected",
        color=0x00FF00
    )
    embed.add_field(name="Hash", value=f"`{tx_data['txid']}`", inline=False)
    embed.add_field(name="Amount", value=f"`{tx_data['amount_ltc']:.8f} LTC (${tx_data['amount_usd']:.2f})`", inline=False)
    embed.add_field(name="Confirmations", value=f"`0/{config['required_confirmations']}`", inline=True)
    
    loading = discord.Embed(
        description="Loading: Awaiting Confirmations...",
        color=0xFFFF00
    )
    
    await channel.send(embed=embed)
    msg = await channel.send(embed=loading)
    
    # Confirmation monitoring
    while tx_data['confirmations'] < config['required_confirmations']:
        await asyncio.sleep(60)
        updated = await monitor.check_transaction(tx_data['txid'])
        if not updated: break
        
        tx_data['confirmations'] = updated['confirmations']
        embed.set_field_at(2, name="Confirmations", value=f"`{tx_data['confirmations']}/{config['required_confirmations']}`")
        await msg.edit(embed=embed)
    
    if tx_data['confirmations'] >= config['required_confirmations']:
        await handle_release(channel)

# Step 7: Funds release
async def handle_release(channel):
    tx_id = next((k for k,v in active_deals.items() if v['channel_id'] == channel.id), None)
    if not tx_id: return
    
    seller_id = active_deals[tx_id].get('seller')
    if not seller_id: return
    
    seller = channel.guild.get_member(seller_id)
    amount = active_deals[tx_id]['amount_usd']
    
    embed = discord.Embed(
        title="✅ Payment Confirmed",
        description=f"`${amount:.2f}` received successfully!",
        color=0x00FF00
    )
    
    class ReleaseView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=None)
        
        @discord.ui.button(label="Release Funds", style=discord.ButtonStyle.green)
        async def release(self, interaction, button):
            if interaction.user.id != seller.id:
                return await interaction.response.send_message("Only the seller can release funds", ephemeral=True)
            await interaction.response.edit_message(view=None)
            await channel.send(f"💰 Funds released by {seller.mention}!")
            del active_deals[tx_id]
            save_data(active_deals)
        
        @discord.ui.button(label="Cancel Deal", style=discord.ButtonStyle.red)
        async def cancel(self, interaction, button):
            if interaction.user.id != seller.id:
                return await interaction.response.send_message("Only the seller can cancel", ephemeral=True)
            await interaction.response.edit_message(view=None)
            await channel.send("❌ Deal cancelled by seller.")
            del active_deals[tx_id]
            save_data(active_deals)
    
    await channel.send(embed=embed, view=ReleaseView())

# Start bot
if __name__ == "__main__":
    import os
    os.makedirs('data', exist_ok=True)
    bot.run(config['bot_token'])
