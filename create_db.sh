#! /usr/bin/env zsh

if [[ -d "/opt/anaconda3" ]]; then
    source /opt/anaconda3/etc/profile.d/conda.sh
elif [[ -d "/home/alex/anaconda3" ]]; then
    source /home/alex/anaconda3/etc/profile.d/conda.sh
elif [[ -d "/Users/alex/anaconda3" ]]; then
    source /Users/alex/anaconda3/etc/profile.d/conda.sh
fi
conda activate nerd-market-maker

echo
echo Creating initial database for NerdMarketMaker application in TEST environment:
python -m market_maker.db.create_tables -e test

echo
python -m market_maker.db.initial_data_setup_test -e test

echo
echo Creating initial database for NerdMarketMaker application in LIVE environment:
python -m market_maker.db.create_tables -e live

echo
python -m market_maker.db.initial_data_setup_live -e live

echo
echo Completed!