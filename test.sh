#! /bin/bash
>&2 echo This should go to stderr
echo Interactive Test
>&2 echo This should also go to stderr
read -p "Hello, who am I speaking to? " NAME
echo It\'s Nice to meet you $NAME
