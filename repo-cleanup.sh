USERNAME="MedicD21"

repos=(
   DAD
   family-wishlist
   HuntShinies
   InnieOutie
   Mars
   module-3-submission
   module4solution
   module5solution
   odin-recipes
   pokecustoms
   pokedex_dushin
   PokeDex_Info
   ppro
   ShinyHunter
   shiny_lumi_map
   Simple_Budget_App
   Star-Detect
)

for repo in "${repos[@]}"
do
    echo "Deleting $repo..."
    gh repo delete "$USERNAME/$repo" --yes
done