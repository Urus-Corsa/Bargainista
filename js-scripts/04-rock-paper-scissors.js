let player = {
  move: undefined,
  score: 0
};
let computer = {
  move: undefined,
  score: 0
};
let gameStats = {
  // roundWinners: {
  //   1: undefined,
  // },
  tiedRounds: 0
};

function setComputersMove(){
  const randomNumber = Math.random();
  // Ternary Operator
  let computersMove = 
  randomNumber < 1/3 ? 'rock' : 
  randomNumber < 2/3 ? 'paper' : 
  'scissors';
  // return computersMove;
  computer.move = computersMove;

  // if(0<=randomNumber && randomNumber<1/3){
  //   console.log('computer picked rock');
  //   return 'rock';
  // } else if(1/3<=randomNumber && randomNumber<2/3){
  //   console.log('computer picked paper');
  //   return 'paper';
  // } else {
  //   console.log('computer picked scissors');
  //   return 'scissors';
  // }
}

function determineWinner(){
  // let roundsPlayed =  Object.keys(gameStats.roundWinners).length;
  setComputersMove();
  let result = `Computer picked ${computer.move}.\n`;
  if(player.move === computer.move){
    gameStats.tiedRounds ++;
    result += 'It\'s a tie!';
  } else if (player.move === 'paper' && computer.move === 'rock' || player.move === 'rock' && computer.move === 'paper'){
    if(computer.move === 'paper'){
      computer.score ++;
      result += 'Computer wins!';
    }else{
      player.score ++;
      result += 'You win!';
    }
  } else if (player.move === 'paper' && computer.move === 'scissors' || player.move === 'scissors' && computer.move === 'paper'){
    if(computer.move === 'scissors'){
      computer.score ++;
      result += 'Computer wins!';
    }else{
      player.score ++;
      result += 'You win!';
    }
  } else if (player.move === 'rock' && computer.move === 'scissors' || player.move === 'scissors' && computer.move === 'rock'){
    if(computer.move === 'rock'){
      computer.score ++;
      result += 'Computer wins!';
    }else{
      player.score ++;
      result += 'You win!';
    }
  } else {
    console.log(`An error has occurred in determineWinner()\nPlayer: ${player.move}\nComputer: ${computer.move}`);
    return;
  }
  result += `\n-------- SCORE BOARD --------\nYour Score: ${player.score}\nComputer's Score: ${computer.score}\nTied Rounds: ${gameStats.tiedRounds}`;
  return result;
}