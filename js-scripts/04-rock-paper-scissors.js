let player;

function getComputersMove(){
  let randomNumber = Math.random()
  if(0<=randomNumber && randomNumber<1/3){
    return 'rock'
  } else if(1/3<=randomNumber && randomNumber<2/3){
    return 'paper'
  } else {
    return 'scissors'
  }
}

function determineWinner(){
  let computer = getComputersMove();
  let result = `Computer picked ${computer}.\n`
  if(player === computer){
    return result+'It is a tie!'
  } else if (player === 'paper' && computer === 'rock' || player === 'rock' && computer === 'paper'){
    if(computer === 'paper'){
      return result+'Computer wins!'
    }else{
      return result+'You win!'
    }
  } else if (player === 'paper' && computer === 'scissors' || player === 'scissors' && computer === 'paper'){
    if(computer === 'scissors'){
      return result+'Computer wins!'
    }else{
      return result+'You win!'
    }
  } else if (player === 'rock' && computer === 'scissors' || player === 'scissors' && computer === 'rock'){
    if(computer === 'rock'){
      return result+'Computer wins!'
    }else{
      return result+'You win!'
    }
  }
}